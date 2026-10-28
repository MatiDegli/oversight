"""Preflight and workspace setup through existing APIs only."""

import asyncio
import math
from pathlib import Path
from typing import Any

from tools.eval.grade import Tariff
from tools.eval.ledger import manifest
from tools.eval.tasks import BenchError, Task, digest, read_template
from tools.eval.transport import API, Events

from oversight_contracts import CONTRACT_VERSION
from oversight_contracts.commands import FileContent, GraphResponse
from oversight_contracts.events import ModelProfile, RunStarted

RUNTIME_BASE = "4ca559617ec80fb7542be3956d71ea80d4ff0ac5"
SETUP_TIMEOUT_S = 30
PROFILES = {
    "muse-spark": ("meta", "muse-spark-1.3-contributor", "low"),
    "muse-spark-open": ("baseten", "meta-models/muse-spark-open-1026", "low"),
    "fake": ("fake", "fake-scripted", None),
}


def profile(run: RunStarted, model: str, reasoning: str | None = "__profile__") -> ModelProfile:
    if (
        run.contract_version != CONTRACT_VERSION
        or run.workspace != "demo-repo"
        or run.base_commit != RUNTIME_BASE
        or not run.prompt_version
    ):
        raise BenchError("unexpected run.started baseline, contract or prompt version")
    advertised = {p.id: p for p in run.models}
    if len(advertised) != len(run.models) or model not in advertised:
        raise BenchError("model is not advertised uniquely")
    expected = dict(PROFILES)
    if reasoning != "__profile__":
        expected[model] = (*PROFILES[model][:2], reasoning)
    if any(
        p.id not in expected or (p.provider, p.model, p.reasoning) != expected[p.id]
        for p in run.models
    ):
        raise BenchError("unexpected advertised model profile")
    if len({s.slot for s in run.slots}) != len(run.slots) or any(
        s.default_model not in advertised for s in run.slots
    ):
        raise BenchError("invalid advertised slots")
    chosen = advertised[model]
    if (chosen.provider, chosen.model, chosen.reasoning) != expected[model]:
        raise BenchError("model profile does not match exact approved model/reasoning")
    if not any(s.slot == "A" for s in run.slots):
        raise BenchError("slot A is not offered")
    budget = run.budget
    if (
        not math.isfinite(budget.spent_usd)
        or budget.spent_usd < 0
        or not math.isfinite(budget.budget_usd)
        or (chosen.provider != "fake" and budget.budget_usd <= 0)
    ):
        raise BenchError("invalid advertised budget")
    limits = run.limits
    if not (
        0 < limits.max_steps <= 30
        and 0 < limits.max_output_tokens <= 4096
        and 0 < limits.max_wall_clock_s <= 600
        and 0 < limits.test_timeout_s <= 60
        and limits.cone_max_depth > 0
        and limits.cone_max_nodes > 0
    ):
        raise BenchError("unexpected advertised limits")
    if model == "fake" and any(p.provider != "fake" for p in run.models):
        raise BenchError("fake bench requires a fake-only server")
    return chosen


def allowance(model: str, tariff: Tariff | None) -> float:
    rates = tariff or (
        Tariff(input=1, output=2) if model == "muse-spark-open" else Tariff(input=0.1, output=0.2)
    )
    # 36 turns is the loop's separate bound, not its 30-tool-step allowance.
    return max(36 * (16000 * rates.input + 4096 * rates.output) / 1e6, 0.0870912)


class Session:
    def __init__(self, api: API, events: Events) -> None:
        self.api, self.events = api, events
        self.clean = read_template()
        self.run: RunStarted
        self.model: ModelProfile

    async def graph(self) -> GraphResponse:
        graph = GraphResponse.model_validate(await self.api.request("GET", "/graph"))
        if graph.run_id != self.events.run_id or graph.graph.parse_errors:
            raise BenchError("unexpected graph run or parse errors")
        expected = {p for p in self.clean if p.endswith(".py")}
        if {n.path for n in graph.graph.nodes} != expected:
            raise BenchError("unexpected Python files in workspace graph")
        return graph

    async def preflight(self, model: str, reasoning: str | None = "__profile__") -> None:
        async with asyncio.timeout(30):
            snapshot = await self.graph()
            started = None
            while self.events.seq < snapshot.seq:
                event = await self.events.next()
                if event.type == "run.started":
                    started = event.payload
                elif event.type != "graph.snapshot":
                    raise BenchError(
                        "occupied or previously used server: start a dedicated fresh one"
                    )
            if started is None or snapshot.graph.commit != started.base_commit:
                raise BenchError("server is not at its clean initial commit")
            self.run = started
            self.model = profile(started, model, reasoning)
            await self.verify(self.clean)

    async def file(self, path: str) -> FileContent:
        file = FileContent.model_validate(
            await self.api.request("GET", "/files", params={"path": path})
        )
        if file.path != path or file.agent_id is not None or file.hash != digest(file.content):
            raise BenchError("file API hash/path mismatch")
        return file

    async def verify(self, expected: dict[str, str]) -> None:
        for name, content in expected.items():
            if (await self.file(name)).content != content:
                raise BenchError(f"unexpected workspace content: {name}")

    async def rollback(
        self, touched: list[str], before: dict[str, str], after: dict[str, str]
    ) -> None:
        for path in reversed(touched):
            current = await self.file(path)
            if current.content == before[path]:
                continue
            if current.content != after[path]:
                raise BenchError("workspace changed during setup rollback; refusing overwrite")
            await self.api.request(
                "PUT",
                "/files",
                json={"path": path, "content": before[path], "base_hash": current.hash},
            )
        await self.api.request("POST", "/graph/rescan")

    async def replace(self, before: dict[str, str], after: dict[str, str]) -> None:
        await self.api.health(self.events.run_id)
        await self.verify(before)
        touched: list[str] = []
        try:
            for path in before:
                if before[path] != after[path]:
                    touched.append(path)  # also covers a lost PUT response after a successful save
                    await self.api.request(
                        "PUT",
                        "/files",
                        json={
                            "path": path,
                            "content": after[path],
                            "base_hash": digest(before[path]),
                        },
                    )
            await self.api.request("POST", "/graph/rescan")
            await self.verify(after)
        except BaseException:
            async with asyncio.timeout(30):
                await self.rollback(touched, before, after)
            raise

    async def setup(self, task: Task) -> tuple[dict[str, str], str]:
        async with asyncio.timeout(SETUP_TIMEOUT_S):
            baseline = task.files()
            await self.replace(self.clean, baseline)
            graph = await self.graph()
            # Drain only our main edits before start. An unrelated agent is never ignored.
            while self.events.seq < graph.seq:
                event = await self.events.next()
                if event.type not in {"graph.delta", "graph.snapshot"}:
                    raise BenchError("workspace became occupied during setup")
            return baseline, graph.graph.commit

    def evidence(
        self, out: Path, tasks: list[Task], tariff: Tariff | None, experiment: dict | None = None
    ) -> None:
        values: dict[str, Any] = {
            "experiment": experiment,
            "run_started": self.run.model_dump(mode="json"),
            "selected_profile": self.model.model_dump(),
            "task_hashes": {t.id: t.hash for t in tasks},
            "baseline_hashes": {t.id: t.baseline_hash for t in tasks},
            "tariff_usd_per_m": tariff.model_dump() if tariff else "unknown",
            "task_allowance_usd": allowance(self.model.id, tariff),
            "planning_rates_usd_per_m": (
                tariff.model_dump() if tariff else {"input": 0.1, "output": 0.2}
            ),
            "planning": {
                "turns": 36,
                "average_input_tokens": 16000,
                "output_tokens_per_turn": 4096,
                "billing_ceiling_guaranteed": False,
            },
            "raw_events": f"events-{self.events.run_id}.jsonl",
        }
        manifest(out.parent / "manifest.json", self.events.run_id, values)
