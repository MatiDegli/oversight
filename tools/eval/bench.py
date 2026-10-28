"""Pilot, project and execute reproducible arm comparisons against one durable ledger."""

import argparse
import asyncio
import json
import secrets
import sys
from pathlib import Path

from tools.eval.arms import PAID_OUTPUT, Arm, load_arms, output_path
from tools.eval.ledger import Ledger
from tools.eval.planning import project, schedule
from tools.eval.report import report
from tools.eval.run import execute, positive
from tools.eval.server import dedicated
from tools.eval.session import allowance
from tools.eval.tasks import BenchError, Task, digest, load_tasks


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


async def run_plan(
    ledger: Ledger,
    arms: list[Arm],
    tasks: list[Task],
    repetitions: int,
    seed: int | None,
    *,
    pilot: bool,
    fake_script: Path | None,
    trace: bool = False,
) -> dict:
    if pilot and (len(tasks) != 1 or repetitions != 1):
        raise BenchError("a pilot requires exactly one task per arm and one repetition")
    for arm in arms:
        if arm.profile == "muse-spark-open" and arm.tariff is None:
            raise BenchError("Baseten requires an explicit input/output tariff")
    ledger.ready()
    specification = {
        "arms": [a.model_dump(mode="json") for a in arms],
        "task_hashes": {t.id: t.hash for t in tasks},
        "repetitions": repetitions,
        "pilot": pilot,
    }
    plan_id = digest(json.dumps(specification, sort_keys=True))
    previous = ledger.state.plans.get(plan_id)
    if previous:
        if seed is not None and seed != previous["seed"]:
            raise BenchError("resume must keep the recorded seed")
        seed = previous["seed"]
    elif seed is None:
        seed = secrets.randbits(32)
    assert seed is not None
    jobs = schedule(arms, tasks, repetitions, seed)
    projection = None if pilot else project(ledger, jobs)
    if pilot:
        remaining = sum(
            allowance(j.arm.profile, j.arm.tariff) for j in jobs if not j.completed(ledger)
        )
        if ledger.spent + remaining > ledger.state.cap_usd:
            raise BenchError("cap cannot reserve pilot allowances")
    ledger.state.plans[plan_id] = {
        **specification,
        "seed": seed,
        "projection": projection,
        "order": [dict(task_id=j.task.id, **j.identity()) for j in jobs],
    }
    ledger.save()  # persist seed and order before any child or paid start
    for job in jobs:
        if job.completed(ledger):
            continue
        if not pilot:
            project(ledger, jobs)  # accumulated costs can invalidate the initial projection
        ledger.check_allowance(
            job.task, job.arm.profile, allowance(job.arm.profile, job.arm.tariff), **job.identity()
        )
        async with dedicated(
            job.arm, ledger.out.parent, ledger.state.cap_usd, fake_script, trace=trace
        ) as server:
            await execute(
                server,
                job.arm.profile,
                ledger.state.cap_usd,
                ledger.out,
                [job.task],
                job.arm.tariff,
                arm=job.arm,
                repetition=job.repetition,
                shared_ledger=ledger,
                pilot=pilot,
                trace=trace,
            )
    return {"plan_id": plan_id, "seed": seed, "projection": projection, "report": report(ledger)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["pilot", "project", "plan", "report"])
    parser.add_argument("--arms", type=Path, help="JSON array of named arm configurations")
    parser.add_argument("--arm", action="append", default=[], help="select named arms")
    parser.add_argument("--task", action="append", default=[])
    parser.add_argument("--repetitions", type=positive_int, default=1)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--cap-usd", type=positive, default=20)
    parser.add_argument("--out", type=Path, default=PAID_OUTPUT)
    parser.add_argument("--fake-script", type=Path)
    parser.add_argument("--trace", action="store_true")
    args = parser.parse_args(argv)
    if args.command != "report" and args.arms is None:
        parser.error("--arms is required")
    ledger = None
    try:
        arms = load_arms(args.arms, args.arm) if args.arms else []
        out = output_path(args.out, paid=any(a.profile != "fake" for a in arms))
        # Reporting can only inspect an existing ledger, never create a replacement by typo.
        if args.command in {"project", "report"} and not (out.parent / "ledger.json").exists():
            raise BenchError("an existing ledger is required")
        tasks = load_tasks()
        if set(args.task) - {t.id for t in tasks}:
            raise BenchError("unknown task id")
        tasks = [t for t in tasks if not args.task or t.id in args.task]
        ledger = Ledger(out, args.cap_usd, None, multi_tariff=True)
        if args.command == "report":
            result = report(ledger)
        elif args.command == "project":
            result = project(ledger, schedule(arms, tasks, args.repetitions, args.seed or 0))
        else:
            result = asyncio.run(
                run_plan(
                    ledger,
                    arms,
                    tasks,
                    args.repetitions,
                    args.seed,
                    pilot=args.command == "pilot",
                    fake_script=args.fake_script,
                    trace=args.trace,
                )
            )
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"bench refused/stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        if ledger:
            ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
