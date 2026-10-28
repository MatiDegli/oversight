"""Seeded experiment schedules and fail-closed pilot projections."""

import random
from dataclasses import dataclass

from tools.eval.arms import Arm
from tools.eval.ledger import Ledger, Record
from tools.eval.tasks import BenchError, Task


@dataclass(frozen=True)
class Job:
    arm: Arm
    task: Task
    repetition: int

    def identity(self) -> dict:
        return dict(
            arm_id=self.arm.arm_id, repetition=self.repetition, config_hash=self.arm.config_hash()
        )

    def completed(self, ledger: Ledger) -> bool:
        return ledger.completed(self.task, self.arm.profile, **self.identity())


def schedule(arms: list[Arm], tasks: list[Task], repetitions: int, seed: int) -> list[Job]:
    if repetitions < 1 or not tasks or not arms:
        raise BenchError("a plan needs arms, tasks and positive repetitions")
    jobs = [
        Job(arm, task, rep) for rep in range(1, repetitions + 1) for task in tasks for arm in arms
    ]
    random.Random(seed).shuffle(jobs)
    return jobs


def priced(record: Record, arm: Arm) -> float:
    if record.result is None:
        raise BenchError("pilot has no result")
    cost = record.result.cost_usd
    if record.unknown or cost == "unknown":
        tokens = record.result.tokens
        if (
            arm.tariff is None
            or not isinstance(tokens.input, int)
            or not isinstance(tokens.output, int)
        ):
            raise BenchError(
                f"unknown tariff/cost for arm {arm.arm_id}; explicit tariff and tokens required"
            )
        cost = (tokens.input * arm.tariff.input + tokens.output * arm.tariff.output) / 1e6
    return max(record.charged_usd, cost)


def project(ledger: Ledger, jobs: list[Job]) -> dict:
    if any(r.status != "finished" for r in ledger.state.records):
        raise BenchError("unsettled reservation blocks projection")
    arms = {j.arm.arm_id: j.arm for j in jobs}
    estimates = {}
    for arm in arms.values():
        ledger.bind(arm.arm_id, arm.config_hash())
        if arm.profile == "muse-spark-open" and arm.tariff is None:
            raise BenchError(f"unknown tariff for arm {arm.arm_id}; supply an explicit tariff")
        pilots = [
            r
            for r in ledger.state.records
            if r.arm_id == arm.arm_id and r.config_hash == arm.config_hash() and r.pilot
        ]
        if not pilots:
            raise BenchError(f"missing pilot for arm {arm.arm_id}")
        estimates[arm.arm_id] = max(priced(r, arm) for r in pilots)
    # Known partial charges remain counted. Explicit pricing can inform a projection but never
    # clears the ledger's unknown flags or authorizes an unsettled paid run.
    spent = ledger.spent
    for record in ledger.state.records:
        if record.unknown:
            if record.arm_id not in arms:
                raise BenchError("ledger has unknown cost outside the selected arms")
            spent += priced(record, arms[record.arm_id]) - record.charged_usd
    pending = [j for j in jobs if not j.completed(ledger)]
    remaining = sum(estimates[j.arm.arm_id] for j in pending)
    total = spent + remaining
    if total > ledger.state.cap_usd:
        raise BenchError(f"projection ${total:.6f} exceeds cap ${ledger.state.cap_usd:.2f}")
    return {
        "cap_usd": ledger.state.cap_usd,
        "spent_usd": spent,
        "remaining_usd": remaining,
        "projected_total_usd": total,
        "remaining_attempts": len(pending),
        "per_arm_usd": estimates,
        "method": "maximum observed pilot cost per arm; estimate, not a billing ceiling",
    }
