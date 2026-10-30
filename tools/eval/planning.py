"""Seeded experiment schedules and fail-closed pilot projections."""

import json
import random
from dataclasses import dataclass
from pathlib import Path

from tools.eval.arms import Arm, validate_pair
from tools.eval.ledger import Ledger, Record, State
from tools.eval.session import allowance
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
    validate_pair(arms)
    if repetitions < 1 or not tasks or not arms:
        raise BenchError("a plan needs arms, tasks and positive repetitions")
    jobs = [
        Job(arm, task, rep) for rep in range(1, repetitions + 1) for task in tasks for arm in arms
    ]
    random.Random(seed).shuffle(jobs)
    return jobs


def estimate(
    out: Path, state: State | None, jobs: list[Job], detection: Path | None, cap: float = 20
) -> dict:
    records = state.records if state else []
    attempts = []
    for job in jobs:
        matching = [r for r in records if r.arm_id == job.arm.arm_id]
        if any(r.config_hash not in {job.arm.config_hash(), "legacy"} for r in matching):
            raise BenchError("arm configuration changed; choose a new arm_id")
        matching = [
            r for r in matching if r.task_id == job.task.id and r.profile == job.arm.profile
        ]
        if any(r.task_hash != job.task.hash for r in matching):
            raise BenchError("task changed after its recorded attempt")
        attempts.append(
            {
                "arm_id": job.arm.arm_id,
                "task_id": job.task.id,
                "repetition": job.repetition,
                "reservation_usd": allowance(job.arm.profile, job.arm.tariff),
                "pending": not any(
                    r.repetition == job.repetition and r.status == "finished" for r in matching
                ),
            }
        )
    spent = sum(r.charged_usd for r in records)
    pending = sum(a["reservation_usd"] for a in attempts if a["pending"])
    result = {
        "method": "estimate, not a billing ceiling",
        "ledger": str(out.parent / "ledger.json"),
        "attempts": len(jobs),
        "reservations": attempts,
        "pending_total_usd": pending,
        "spent_usd": spent,
        "remaining_usd": (state.cap_usd if state else cap) - spent,
        "estimated_total_usd": spent + pending,
        "rate_basis": {
            j.arm.arm_id: "arm tariff"
            if j.arm.tariff
            else "default planning rates 0.1/0.2 USD per M, assumed"
            for j in jobs
        },
    }
    if detection:
        data = json.loads(detection.read_text())
        if Path(data["ledger"]).resolve() != (out.parent / "ledger.json").resolve():
            raise BenchError("detection estimate uses a foreign ledger")
        detection_pending = float(data["estimated_total_usd"]) - float(data["spent_usd"])
        if not 0 <= detection_pending < float("inf"):
            raise BenchError("invalid detection pending estimate")
        combined = spent + pending + detection_pending
        result.update(
            detection_pending_usd=detection_pending,
            combined_total_usd=combined,
            within_usd20=combined <= 20,
        )
    return result


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
