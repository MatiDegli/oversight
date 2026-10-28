"""Deterministic grading in a scratch copy, never the agent's tests or workspace."""

import asyncio
import contextlib
import os
import signal
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from tools.eval.identity import Identity
from tools.eval.tasks import BenchError, Task, resource, safe_path

from oversight_contracts.events import ChangeProposed, FileDiff, UsageTick

Unknown = Literal["unknown"]
Count = Annotated[int, Field(ge=0, strict=True)] | Unknown
Cost = Annotated[float, Field(ge=0, allow_inf_nan=False)] | Unknown
Verdict = Literal["passed", "failed", "not_run"]
REAP_TIMEOUT_S = 5


class Tokens(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: Count
    output: Count


class Tests(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline: Verdict
    hidden: Verdict
    policy: Verdict


class Result(Identity):
    model_config = ConfigDict(extra="forbid")
    profile: str = Field(default="legacy", frozen=True)
    task_id: str
    agent_model: str
    outcome: Literal["solved", "failed", "stopped"]
    turns: Count = "unknown"
    steps: int = Field(ge=0)
    duration_s: float = Field(ge=0, allow_inf_nan=False)
    tokens: Tokens
    cost_usd: Cost
    test_result: Tests


class Tariff(BaseModel):
    """Explicit approved USD per million; completion includes reasoning tokens."""

    input: float = Field(ge=0, allow_inf_nan=False)
    output: float = Field(ge=0, allow_inf_nan=False)


def call_cost(tick: UsageTick, tariff: Tariff | None) -> Cost:
    for count in (tick.input_tokens, tick.output_tokens, tick.cached_tokens):
        if count is not None and count < 0:
            raise BenchError("negative usage")
    if tick.cost_usd is not None:
        if not 0 <= tick.cost_usd < float("inf"):
            raise BenchError("invalid cost")
        return tick.cost_usd
    if tariff and tick.input_tokens is not None and tick.output_tokens is not None:
        # Price all input as uncached: no unapproved cache discount assumption.
        return (tick.input_tokens * tariff.input + tick.output_tokens * tariff.output) / 1e6
    return "unknown"


class Usage:
    def __init__(self, tariff: Tariff | None = None) -> None:
        self.tariff = tariff
        self.incomplete = False
        self.inputs: list[int | None] = []
        self.outputs: list[int | None] = []
        self.costs: list[Cost] = []

    def add(self, tick: UsageTick) -> Cost:
        cost = call_cost(tick, self.tariff)
        self.inputs.append(tick.input_tokens)
        self.outputs.append(tick.output_tokens)
        self.costs.append(cost)
        return cost

    @property
    def tokens(self) -> Tokens:
        def total(counts: list[int | None]) -> Count:
            if self.incomplete:
                return "unknown"
            return (
                sum(n for n in counts if n is not None)
                if counts and None not in counts
                else "unknown"
            )

        return Tokens(input=total(self.inputs), output=total(self.outputs))

    @property
    def cost(self) -> Cost:
        if self.incomplete or not self.costs or "unknown" in self.costs:
            return "unknown"
        return sum(c for c in self.costs if isinstance(c, (float, int)))


def candidate_files(
    baseline: dict[str, str], updates: dict[str, FileDiff], proposal: ChangeProposed, task: Task
) -> dict[str, str]:
    """A complete proposal must match the final full-content updates, including reversions."""
    candidate = dict(baseline)
    stats = {f.path: f for f in proposal.files}
    if len(stats) != len(proposal.files):
        raise BenchError("duplicate proposed path")
    for name, diff in updates.items():
        safe_path(name)
        if diff.path != name or diff.original != baseline.get(name):
            raise BenchError("diff original does not match verified task baseline")
        if diff.modified == diff.original:
            continue
        if name not in task.allowed_changed_paths:
            raise BenchError(f"protected path changed: {name}")
        if diff.modified is None or diff.original is None or diff.status != "modified":
            raise BenchError("only modifications to existing source files are allowed")
        candidate[name] = diff.modified
    changed = {p for p in candidate if candidate[p] != baseline[p]}
    if changed != set(stats):
        raise BenchError("proposal file list differs from complete changed-file evidence")
    if any(stats[p].status != "modified" for p in changed):
        raise BenchError("proposal status disagrees with candidate")
    return candidate


async def suite(root: Path, checks: list[str], evidence: Path, deadline_s: float = 30) -> bool:
    """No inherited plugins, user config, provider keys or git hooks in the grading process."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "PYTEST_"))}
    for key in ("META_API_KEY", "BASETEN_API_KEY", "PYTHONHOME"):
        env.pop(key, None)
    env.update(
        PYTHONPATH=str(root), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1"
    )
    evidence.parent.mkdir(parents=True, exist_ok=True)
    with evidence.open("wb") as output:
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-c",
            str(root / "pyproject.toml"),
            "--rootdir",
            str(root),
            "--confcutdir",
            str(root),
            *checks,
            cwd=root,
            env=env,
            stdout=output,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return await asyncio.wait_for(child.wait(), deadline_s) == 0
        except TimeoutError:
            return False
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            try:
                await asyncio.wait_for(child.wait(), REAP_TIMEOUT_S)
            except TimeoutError as exc:
                raise BenchError(
                    f"grading process {child.pid} did not exit after SIGKILL within "
                    f"{REAP_TIMEOUT_S}s; refusing to continue; inspect the process manually"
                ) from exc


async def check_suites(files: dict[str, str], task: Task, evidence: Path) -> Tests:
    with tempfile.TemporaryDirectory(prefix="oversight-grade-") as directory:
        root = Path(directory)
        for name, content in files.items():
            target = root / safe_path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode())
        original = await suite(root, ["tests"], evidence / "original.txt")
        hidden = await suite(
            root, [str(resource(p, "checks")) for p in task.hidden_checks], evidence / "hidden.txt"
        )
    return Tests(
        baseline="passed" if original else "failed",
        hidden="passed" if hidden else "failed",
        policy="passed",
    )


async def grade(
    baseline: dict[str, str],
    updates: dict[str, FileDiff],
    proposal: ChangeProposed,
    task: Task,
    evidence: Path,
) -> Tests:
    try:
        files = candidate_files(baseline, updates, proposal, task)
    except BenchError as error:
        await asyncio.to_thread(evidence.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread((evidence / "policy.txt").write_text, str(error) + "\n")
        return Tests(baseline="not_run", hidden="not_run", policy="failed")
    return await check_suites(files, task, evidence)
