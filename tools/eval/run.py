"""Run independent tasks once through a dedicated Oversight server, with a durable cap."""

import argparse
import asyncio
import contextlib
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Also support `python tools/eval/run.py` from any working directory.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.eval.arms import Arm, output_path
from tools.eval.grade import Result, Tariff, Tests, Usage, grade
from tools.eval.identity import Identity
from tools.eval.ledger import Ledger, Record
from tools.eval.session import PROFILES, Session, allowance
from tools.eval.tasks import BenchError, Task, load_tasks
from tools.eval.timing import attempt_trace, observed
from tools.eval.transport import API, Events

from oversight.telemetry import annotate, mark, span
from oversight_contracts.commands import AgentCreated
from oversight_contracts.events import ChangeProposed, FileDiff


@dataclass
class Attempt:
    task: Task
    record: Record
    usage: Usage
    started: float = field(default_factory=time.monotonic)
    steps: int = 0
    updates: dict[str, FileDiff] = field(default_factory=dict)
    proposal: ChangeProposed | None = None
    base_commit: str | None = None
    tests: Tests = field(
        default_factory=lambda: Tests(baseline="not_run", hidden="not_run", policy="not_run")
    )
    terminal: str | None = None
    resolved: bool = False
    interrupted: bool = False
    brake: bool = False
    error: BaseException | None = None

    def observe(self, event: Any, ledger: Ledger) -> None:
        payload = event.payload
        agent = getattr(payload, "agent_id", None)
        if agent is not None and agent != self.record.agent_id:
            raise BenchError("another agent appeared on the dedicated bench server")
        if event.type == "agent.status":
            self.base_commit = payload.base_commit
        if event.type == "usage.tick":
            self.brake = ledger.charge(self.record, self.usage.add(payload)) or self.brake
        elif event.type == "agent.step":
            self.steps += 1
        elif event.type == "change.updated":
            if payload.change_id != self.record.change_id:
                raise BenchError("unexpected change id")
            self.updates[payload.file.path] = payload.file
        elif event.type == "agent.status" and payload.state in {
            "ready_for_review",
            "halted",
            "stopped",
            "done",
        }:
            if self.terminal is None or payload.state == "stopped":
                self.terminal = payload.state
        elif event.type == "change.resolved":
            if payload.change_id != self.record.change_id or payload.decision != "rejected":
                raise BenchError("unexpected resolution: the bench only rejects")
            self.resolved = True
            self.interrupted = self.interrupted or payload.reason == "stopped"

    def result(self, model: str, elapsed: float) -> Result:
        passed = all(v == "passed" for v in self.tests.model_dump().values())
        solved = self.terminal == "ready_for_review" and passed and not self.interrupted
        return Result(
            **{key: getattr(self.record, key) for key in Identity.model_fields},
            profile=self.record.profile,
            turns=len(self.usage.inputs),
            task_id=self.task.id,
            agent_model=model,
            outcome="stopped" if self.interrupted else "solved" if solved else "failed",
            steps=self.steps,
            duration_s=elapsed,
            tokens=self.usage.tokens,
            cost_usd=self.usage.cost,
            test_result=self.tests,
        )


async def monitor(
    session: Session,
    attempt: Attempt,
    ledger: Ledger,
    baseline: dict[str, str],
    base_commit: str,
    evidence: Path,
) -> None:
    while not attempt.terminal and not attempt.resolved:
        with span("wait_event"):
            event = await session.events.next()
        observed(event)
        attempt.observe(event, ledger)
        if event.type == "usage.tick" and event.payload.model not in {None, session.model.model}:
            raise BenchError("usage reports an unexpected exact model")
        if attempt.brake:
            attempt.interrupted = True
            return
        if event.type == "change.proposed":
            proposal = event.payload
            if (
                proposal.change_id != attempt.record.change_id
                or proposal.task != attempt.task.prompt
                or proposal.base_commit != base_commit
                or proposal.revision != 1
            ):
                raise BenchError("proposal does not match this task's start")
            attempt.proposal = proposal
            with span("grading"):
                attempt.tests = await grade(
                    baseline, attempt.updates, proposal, attempt.task, evidence
                )
            mark("grading_done")


async def cleanup(session: Session, attempt: Attempt, ledger: Ledger) -> None:
    if attempt.resolved:
        return
    # Cancellation may have closed the async generator while it was waiting for an SSE byte.
    await session.events.reconnect()
    if (
        attempt.terminal in {"ready_for_review", "halted"}
        and not attempt.interrupted
        and (attempt.proposal or attempt.base_commit)
    ):
        await session.api.request(
            "POST",
            f"/changes/{attempt.record.change_id}/reject",
            json={
                "comment": "Task bench: graded; never merge.",
                "revision": attempt.proposal.revision if attempt.proposal else 0,
                "head_commit": attempt.proposal.head_commit
                if attempt.proposal
                else attempt.base_commit,
            },
        )
    else:
        await session.api.request("POST", f"/agents/{attempt.record.agent_id}/stop")
    while not attempt.resolved:
        attempt.observe(await session.events.next(), ledger)


async def start_once(session: Session, attempt: Attempt, ledger: Ledger) -> None:
    """Finish the single start request even if cancelled while its response is in flight."""
    pending = asyncio.create_task(
        session.api.request(
            "POST",
            "/agents",
            json={"task": attempt.task.prompt, "slot": "A", "model": session.model.id},
        )
    )
    cancelled = False
    try:
        response = await asyncio.shield(pending)
    except asyncio.CancelledError:
        cancelled = True
        response = await pending
    created = AgentCreated.model_validate(response)
    attempt.record.agent_id, attempt.record.change_id = created.agent_id, created.change_id
    ledger.save()
    if created.slot != "A":
        raise BenchError("server started the wrong slot")
    if cancelled:
        raise asyncio.CancelledError


async def run_task(
    session: Session,
    task: Task,
    ledger: Ledger,
    tariff: Tariff | None,
    *,
    arm: Arm,
    repetition: int = 1,
    pilot: bool = False,
) -> bool:
    identity: dict[str, Any] = dict(
        arm_id=arm.arm_id,
        repetition=repetition,
        config_hash=arm.config_hash(session.run),
    )
    ledger.check_allowance(task, session.model.id, allowance(session.model.id, tariff), **identity)
    baseline, base_commit = await session.setup(task)
    record = ledger.reserve(
        task,
        session.model.id,
        session.run.run_id,
        allowance(session.model.id, tariff),
        tariff=tariff,
        pilot=pilot,
        **identity,
    )
    attempt = Attempt(task, record, Usage(tariff))
    evidence = ledger.out.parent / session.run.run_id / task.id / record.attempt_id
    cleaned = False
    with attempt_trace(session.api, session.run.run_id, ledger.out.parent, record.attempt_id):
        await _observe_attempt(session, attempt, ledger, baseline, base_commit, evidence)
    error = attempt.error
    if attempt.interrupted and attempt.terminal not in {"ready_for_review", "halted"}:
        # A cancelled in-flight call can still be billed without a usage.tick.
        attempt.usage.incomplete = True
    elapsed = time.monotonic() - attempt.started
    try:
        async with asyncio.timeout(90):
            if record.agent_id:
                await cleanup(session, attempt, ledger)
                cleaned = True
            if cleaned:
                await session.replace(baseline, session.clean)
                # Drain trailing resolution/status and restore graph events.
                graph = await session.graph()
                while session.events.seq < graph.seq:
                    attempt.observe(await session.events.next(), ledger)
    except BaseException as exc:
        cleaned = False
        error = error or exc
    if attempt.terminal == "stopped":
        attempt.usage.incomplete = True
    ledger.finish(record, attempt.result(session.model.model, elapsed), cleaned=cleaned)
    if error:
        raise error
    return attempt.brake or attempt.usage.cost == "unknown"


async def _observe_attempt(
    session: Session,
    attempt: Attempt,
    ledger: Ledger,
    baseline: dict[str, str],
    base_commit: str,
    evidence: Path,
) -> None:
    try:
        with span("start_request"):
            await start_once(session, attempt, ledger)
        annotate(agent=attempt.record.agent_id)
        with span("monitor"):
            async with asyncio.timeout(session.run.limits.max_wall_clock_s + 150):
                await monitor(session, attempt, ledger, baseline, base_commit, evidence)
    except BaseException as exc:
        attempt.error = exc
        attempt.interrupted = True


def configured_arm(model: str, tariff: Tariff | None, arm: Arm | None) -> Arm:
    if arm is None:
        arm = Arm.model_validate(
            dict(
                arm_id=model,
                profile=model,
                reasoning=PROFILES[model][2],
                harness_pack="off",
                tariff=tariff,
            )
        )
    if arm.profile != model or arm.tariff != tariff:
        raise BenchError("arm profile/tariff differs from the requested attempt")
    if model == "muse-spark-open" and tariff is None:
        raise BenchError("Baseten requires an explicitly approved input/output tariff")
    if model != "fake" and tariff and (tariff.input <= 0 or tariff.output <= 0):
        raise BenchError("paid profiles require positive tariffs")
    return arm


async def execute(
    server: str,
    model: str,
    cap: float,
    out: Path,
    tasks: list[Task],
    tariff: Tariff | None = None,
    *,
    trace: bool = False,
    arm: Arm | None = None,
    repetition: int = 1,
    shared_ledger: Ledger | None = None,
    pilot: bool = False,
) -> None:
    if not math.isfinite(cap) or cap <= 0:
        raise BenchError("--cap-usd must be finite and positive")
    if model not in PROFILES:
        raise BenchError("unknown model profile")
    if model != "fake" and (shared_ledger is None or arm is None):
        raise BenchError("paid runs require the pilot/plan interface and its cumulative projection")
    arm = configured_arm(model, tariff, arm)
    out = output_path(out, paid=model != "fake")
    if shared_ledger is not None and (
        shared_ledger.out != out or shared_ledger.state.cap_usd != cap
    ):
        raise BenchError("attempt must use the shared ledger's output and cap")
    ledger = shared_ledger or Ledger(out, cap, tariff)
    identity: dict[str, Any] = dict(
        arm_id=arm.arm_id,
        repetition=repetition,
        config_hash=arm.config_hash(),
    )
    api: API | None = None
    events: Events | None = None
    try:
        ledger.ready()  # fail before contacting the server if an earlier run is unsettled
        remaining = [t for t in tasks if not ledger.completed(t, model, **identity)]
        if not remaining:
            return
        api = API(server)
        api.trace_enabled = trace
        health = await api.health()
        events = Events(api, health.run_id, out.parent / f"events-{health.run_id}.jsonl")
        session = Session(api, events)
        await session.preflight(model, arm.reasoning)
        if arm.config_hash(session.run) != arm.config_hash():
            raise BenchError("server limits/prompt do not match the planned configuration")
        session.evidence(
            out,
            tasks,
            tariff,
            {
                **identity,
                "configuration": arm.configuration(session.run),
                "pilot": pilot,
            },
        )
        for task in remaining:
            if await run_task(
                session, task, ledger, tariff, arm=arm, repetition=repetition, pilot=pilot
            ):
                raise BenchError("bench stopped: unknown cost or task/cap allowance reached")
    finally:
        if events:
            with contextlib.suppress(Exception):
                await events.close()
        if api:
            await api.client.aclose()
        if shared_ledger is None:
            ledger.close()


def positive(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be finite and positive") from exc
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return number


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in {"pilot", "project", "plan", "report"}:
        from tools.eval.bench import main as bench_main

        return bench_main(args)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    parser.add_argument("--model", required=True, choices=list(PROFILES))
    parser.add_argument("--cap-usd", required=True, type=positive)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--trace",
        action="store_true",
        help="write timing JSONL (server OVERSIGHT_EVAL_TRACE_DIR must match --out parent)",
    )
    parser.add_argument("--task", action="append", default=[], help="run only named task ids")
    parser.add_argument("--input-usd-per-m", type=float)
    parser.add_argument("--output-usd-per-m", type=float)
    args = parser.parse_args(argv)
    try:
        tariff = None
        if args.input_usd_per_m is not None or args.output_usd_per_m is not None:
            if args.input_usd_per_m is None or args.output_usd_per_m is None:
                raise BenchError("both input and output tariffs are required")
            tariff = Tariff(input=args.input_usd_per_m, output=args.output_usd_per_m)
        if args.model == "muse-spark-open" and tariff is None:
            raise BenchError("Baseten requires an explicitly approved input/output tariff")
        tasks = load_tasks()
        if args.task:
            if set(args.task) - {t.id for t in tasks}:
                raise BenchError("unknown task id")
            tasks = [t for t in tasks if t.id in args.task]
        asyncio.run(
            execute(
                args.server, args.model, args.cap_usd, args.out, tasks, tariff, trace=args.trace
            )
        )
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"bench refused/stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
