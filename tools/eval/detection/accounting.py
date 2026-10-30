"""Detection uses the existing locked ledger and its immutable reservation identities."""

from tools.eval.behavior.catalog import Patch
from tools.eval.detection.config import Arm, fingerprint, worst_case_per_job
from tools.eval.detection_types import DetectionEvidence
from tools.eval.grade import Tariff, Usage
from tools.eval.ledger import Ledger, Record
from tools.eval.tasks import BenchError, Task

from oversight.agents import DecisionUnavailable, ModelReply
from oversight_contracts.events import UsageTick


class FrozenTask(Task):
    @property
    def hash(self) -> str:
        return self.baseline_hash


def task_for(patch: Patch) -> Task:
    return FrozenTask(
        id=f"behavior:{patch.id}",
        title=patch.id,
        prompt=patch.task,
        baseline_revision="frozen-v1",
        baseline_hash=patch.sha256,
        initial_original_suite="passed",
        seeded_defect=None,
        allowed_changed_paths=[],
        hidden_checks=[],
        reference_solution="",
        shortcut_tempting=False,
    )


def completed(ledger: Ledger, patch: Patch, arm: Arm, config: dict, rep: int) -> bool:
    return ledger.completed(task_for(patch), arm.profile, arm.id, rep, fingerprint(config))


class Budget:
    def __init__(
        self, ledger: Ledger, record: Record, tariff: Tariff, per_send: float, *, fake: bool
    ) -> None:
        self.ledger, self.record, self.per_send, self.fake = ledger, record, per_send, fake
        self.usage = Usage(tariff)
        self.receipts = 0
        self.sends = 0
        self.admitted = 0

    async def admission(self) -> None:
        if any(r.unknown for r in self.ledger.state.records):
            raise DecisionUnavailable("ledger_unknown")
        if any(r.status != "finished" and r is not self.record for r in self.ledger.state.records):
            raise DecisionUnavailable("ledger_unreliable")
        if self.sends != self.receipts:
            raise DecisionUnavailable("ledger_unreliable")
        if (
            self.record.charged_usd + self.per_send > self.record.allowance_usd
            or self.ledger.spent + self.per_send > self.ledger.state.cap_usd
        ):
            raise DecisionUnavailable("budget_blocked")
        self.admitted += 1
        if self.fake:
            self.sends += 1
        self.persist()

    def sent(self) -> None:
        self.sends += 1
        self.persist()

    def unsent(self) -> None:
        self.sends -= 1
        self.persist()

    def persist(self) -> None:
        self.ledger.state.plans.setdefault("detection-receipts", {})[self.record.attempt_id] = {
            "admitted": self.admitted,
            "sends": self.sends,
            "receipts": self.receipts,
        }
        self.ledger.save()

    async def accounting(self, reply: ModelReply) -> None:
        tick = UsageTick(
            agent_id="agent-1",
            model=reply.model or "unknown",
            input_tokens=reply.usage.input_tokens,
            output_tokens=reply.usage.output_tokens,
            cached_tokens=reply.usage.cached_tokens,
            cost_usd=0 if self.fake else None,
            spent_usd=self.ledger.spent,
            budget_usd=self.ledger.state.cap_usd,
            brake="ok",
        )
        cost = self.usage.add(tick)
        self.receipts += 1
        self.ledger.charge(self.record, cost)
        self.persist()  # synchronous durable writes: cancellation cannot interrupt a receipt


def pilot_projection(ledger: Ledger, jobs: list[tuple[Patch, Arm, dict, int]]) -> dict:
    ledger.ready()
    estimates = {}
    worst = {}
    admitted = {}
    for _, arm, config, _ in jobs:
        if arm.id in estimates:
            continue
        ledger.bind(arm.id, fingerprint(config))
        pilots = [r for r in ledger.state.records if r.arm_id == arm.id and r.pilot]
        if (
            len(pilots) != 1
            or pilots[0].repetition != 1
            or pilots[0].task_id not in {f"behavior:d{i:02}" for i in range(1, 5)}
        ):
            raise BenchError(f"one d01-d04 pilot required for {arm.id}")
        pilot = pilots[0]
        if (
            pilot.config_hash != fingerprint(config)
            or pilot.result is None
            or pilot.result.detection is None
            or pilot.result.cost_usd == "unknown"
        ):
            raise BenchError("pilot configuration, prompt hash, or accounting differs")
        require_model_call(arm, pilot.result.detection, pilot.result.steps)
        estimates[arm.id] = max(pilot.charged_usd, pilot.result.cost_usd)
        worst[arm.id] = worst_case_per_job(config)
        admitted[arm.id] = max(estimates[arm.id], worst[arm.id])
    pending = [j for j in jobs if not completed(ledger, *j)]
    remaining = sum(admitted[arm.id] for _, arm, _, _ in pending)
    if ledger.spent + remaining > ledger.state.cap_usd:
        raise BenchError("pilot projection exceeds cap; reduce repetitions before dropping arms")
    return {
        "spent_usd": ledger.spent,
        "remaining_usd": remaining,
        "projected_total_usd": ledger.spent + remaining,
        "per_arm_usd": admitted,
        "pilot_per_job_usd": estimates,
        "worst_case_per_job_usd": worst,
        "remaining_attempts": len(pending),
        "method": "max(observed pilot cost, configured worst case); not a billing ceiling",
    }


def require_model_call(arm: Arm, evidence: DetectionEvidence, sends: int) -> None:
    if arm.role != "tests-only" and (
        sends == 0 or not any(d.diagnostics is not None for d in evidence.decisions)
    ):
        raise BenchError(f"{arm.id}: pilot recorded no model call (no diagnostics)")
