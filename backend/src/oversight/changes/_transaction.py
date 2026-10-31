"""Rules' one-shot transaction. Production admission remains closed until P3b."""

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, replace

from oversight.changes import _approval, _candidate, _delivery, _merge, _recovery, _rules, _spend
from oversight.changes._evidence import WriteEvidence
from oversight.changes._grants import Measurement
from oversight.changes._policy import merges_alone
from oversight.changes._resolution_validation import (
    validate_resolution_evidence,
    validate_resolution_intent,
)
from oversight.changes._state import ChangeRecord
from oversight.changes._support import ChangeContext, RulesFailure
from oversight.events import EventRejected, StoredEvent
from oversight.workspace import (
    IntegrityError,
    IsolationUnavailable,
    MergeGuard,
    SuiteRun,
    SuiteUnavailable,
    verify_merge_guard,
)
from oversight_contracts.events import ChangeResolved
from oversight_contracts.grants import GrantUse, covered_rule
from oversight_contracts.policy import ChangeAssessment, EvidenceRef, RuleAssessment


class CandidateVeto(RulesFailure):
    """A persisted rule failure is a nonterminal assessment, not a second error event."""


@dataclass
class Transaction:
    ctx: ChangeContext
    reservation: _rules.RulesReservation
    pre: str
    tree: str | None = None
    guard: MergeGuard | None = None
    barriers_ran: bool = False
    definitive: WriteEvidence | None = None
    measured: tuple[RuleAssessment, ...] = ()
    tested_tree: str | None = None
    grant_measurement: Measurement | None = None
    spend_pin: StoredEvent | None = None

    async def measure(self, guard: MergeGuard) -> None:
        pending = self.reservation.evidence.assessment
        assert pending is not None
        wait = self.reservation.wait
        paths = self.ctx.assessment.path_sources(wait)
        if paths is None or wait.proposal_seq is None:
            raise RulesFailure("policy_invalid", "Protected-path capture is no longer confirmed.")
        measured = await _candidate.measure(
            self.ctx,
            guard,
            pending,
            wait.rec.task.limits,
            protected_paths=paths[0],
            proposal_seq=wait.proposal_seq,
        )
        self.measured, self.grant_measurement = measured.rules, measured.grants

    def grants_for(self, assessment: ChangeAssessment) -> tuple[GrantUse, ...]:
        if self.grant_measurement is None:
            return ()
        return self.ctx.grants.uses(
            self.ctx, assessment, self.grant_measurement, only=self.reservation.evidence.grants
        )

    def revalidate(self) -> None:
        failure = _rules.revalidate(self.ctx, self.reservation)
        if not _rules.current(self.ctx, self.reservation) or failure:
            raise RulesFailure(*(failure or ("target_changed", "Rules reservation expired.")))
        if any(
            not _spend.admits(self.ctx, rule.rule, ref, self.spend_pin)
            for rule in self.measured
            for ref in rule.evidence
        ):
            raise RulesFailure("policy_invalid", "Candidate evidence is no longer confirmed.")
        if self.definitive is not None:
            final = self.definitive.assessment
            if (
                final is None
                or not merges_alone(final, self.definitive.grants)
                or any(rule not in final.rules for rule in self.measured)
            ):
                raise RulesFailure(
                    "policy_invalid",
                    "Definitive evidence or candidate rules are no longer confirmed.",
                )
        if self.reservation.evidence.grants and self.measured:
            pending = self.reservation.evidence.assessment
            assert pending is not None
            current = (
                self.definitive.assessment
                if self.definitive is not None
                else pending.model_copy(
                    update={
                        "rules": [
                            {r.rule: r for r in self.measured}.get(r.rule, r) for r in pending.rules
                        ]
                    }
                )
            )
            assert current is not None
            grants = self.grants_for(current)
            cone = next(r for r in current.rules if r.rule == "small_cone")
            if cone.result != "pass" and not covered_rule(current, cone, grants):
                raise RulesFailure(
                    "policy_invalid",
                    "The candidate wait is no longer covered by the reserved grant.",
                )
            if self.definitive is not None:
                self.definitive = replace(self.definitive, grants=grants)

    async def ready(self) -> None:
        await _spend.ready(self.ctx)
        self.revalidate()

    async def metadata(self) -> None:
        """Reuse C's monotonic latch for a metadata discrepancy outside the suite too."""
        assert self.guard is not None
        try:
            await asyncio.to_thread(self.guard.verify, time.monotonic() + self.ctx.timeout)
        except IntegrityError as error:
            self.ctx.workspace.integrity.suspend(error, uncertain=False)
            raise

    async def barriers(self) -> None:
        """L48: verify metadata/index before queries can refresh them, at both boundaries."""
        assert self.guard is not None
        await self.metadata()
        await verify_merge_guard(
            self.ctx.workspace,
            self.ctx.workspace.main,
            self.guard,
            tested_tree=self.tested_tree,
            timeout_s=self.ctx.timeout,
        )
        await self.ready()
        self.barriers_ran = True

    @asynccontextmanager
    async def authorize(self, guard: MergeGuard, run: SuiteRun) -> AsyncGenerator[None]:
        ctx, rec = self.ctx, self.reservation.wait.rec
        # Suite released its lease; never wait for mode while retaining that lease.
        self.tested_tree = run.tested_tree
        async with ctx.mode.held(), ctx.workspace.git_lease():
            await self.ready()
            await self.barriers()
            await self.ready()
            await self.persist(guard, run)
            await self.ready()
            failure = await _approval.rules_tree(ctx, self.reservation)
            if failure:
                raise RulesFailure(*failure)
            # Git queries await: repeat the synchronous authorization at the effect boundary.
            await self.ready()
            assert self.definitive is not None
            validate_resolution_intent(self.definitive)
            await self.barriers()
            await self.ready()
            assert self.definitive.assessment is not None
            if any(
                not _spend.admits(ctx, rule.rule, ref, self.spend_pin)
                for rule in self.definitive.assessment.rules
                for ref in rule.evidence
            ):
                raise RulesFailure("policy_invalid", "Definitive evidence is no longer available.")
            validate_resolution_intent(self.definitive)
            rec.evidence = self.definitive
            yield

    async def persist(self, guard: MergeGuard, run: SuiteRun) -> None:
        ctx, rec = self.ctx, self.reservation.wait.rec
        pending = self.reservation.evidence.assessment
        assert pending is not None
        seq = next((seq for seq, saved in rec.suite_runs.items() if saved == run), None)
        if seq is None or seq not in ctx.assessment.events or run.problems:
            raise RulesFailure(
                "integrity_invalid"
                if any("code=integrity_invalid" in p for p in run.problems)
                else "suite_failed",
                "Combined suite evidence is unconfirmed or incomplete.",
            )
        suite = rec.executions[seq].suite
        passed = (
            suite.outcome == "passed" and suite.passed > 0 and not (suite.failed or suite.skipped)
        )
        combined = RuleAssessment(
            rule="combined_tests",
            result="pass" if passed else "fail",
            observed=suite.model_dump(mode="json"),
            threshold=next(r.threshold for r in pending.rules if r.rule == "combined_tests"),
            evidence=[EvidenceRef(event_seq=seq)],
        )
        await self.ready()
        spend, self.spend_pin = _spend.guard(ctx, ctx.assessment.inputs(self.reservation.wait))
        final = ChangeAssessment.model_validate(
            {
                **pending.model_dump(),
                "candidate_tree": guard.candidate_tree,
                "rules": [
                    {r.rule: r for r in (*self.measured, combined, spend)}.get(r.rule, r)
                    for r in pending.rules
                ],
            }
        )
        receipt = await ctx.publish(final)
        if not isinstance(receipt, StoredEvent):
            raise RulesFailure("delivery_failed", "Definitive assessment receipt is unconfirmed.")
        grants = self.grants_for(final)
        if not merges_alone(final, grants):
            refusal = RulesFailure if not passed else CandidateVeto
            raise refusal(
                "suite_failed" if not passed else "policy_invalid",
                "Tested combination did not satisfy the rules policy.",
            )
        self.definitive = replace(
            self.reservation.evidence,
            assessment=final,
            reasons=("Tested rules satisfied; standing grants recorded.",)
            if grants
            else ("All rules passed on the tested combination.",),
            grants=grants,
        )

    async def record(self, post: str) -> None:
        """Prepare rules' payload and graph projection; delivery.finish owns all delivery."""
        ctx, rec, evidence = self.ctx, self.reservation.wait.rec, self.definitive
        assert evidence is not None
        resolution = ChangeResolved(
            change_id=evidence.change_id,
            agent_id=evidence.agent_id,
            revision=evidence.revision,
            head_commit=evidence.head_commit,
            decision="approved",
            decided_by="rules",
            reason=None,
            comment=None,
            merge="merged",
            merge_commit=post,
            conflict=None,
            assessment=evidence.assessment,
            reasons=list(evidence.reasons),
            grants=list(evidence.grants) if evidence.grants else None,
        )
        validate_resolution_evidence(resolution, evidence)
        await _delivery.project_merge(ctx, rec, post)
        rec.merged, rec.resolution = True, resolution


async def run(ctx: ChangeContext, rec: ChangeRecord, evidence: WriteEvidence) -> None:
    """Caller owns main/worktree; every recovery settles before those locks are released."""
    wait = ctx.assessment.waits[rec.change_id]
    reservation = _rules.RulesReservation(wait, evidence, rec.decision, wait.spend_pin)
    assert evidence.assessment is not None
    tx = Transaction(ctx, reservation, evidence.assessment.main_commit)
    try:
        try:
            await _delivery.drain_pending(ctx)
            async with ctx.workspace.git_lease():
                tx.tree = (
                    await ctx.git(ctx.workspace.main, "rev-parse", f"{tx.pre}^{{tree}}")
                ).strip()
                await _recovery.verify_rules_abort(ctx, rec, tx.pre, tx.tree)
            await _merge.merge(ctx, rec, None, tx.pre, transaction=tx)
        except EventRejected as error:
            # A refused assessment has no effect; a refused resolution retains the landed merge.
            if not rec.merged:
                rec.fallback = "broken"
                await settle(tx, error)
            else:
                await _delivery.rejected(ctx, rec, error)
        except (Exception, asyncio.CancelledError) as error:
            rec.fallback = "broken"
            await settle(tx, error)
            if isinstance(error, asyncio.CancelledError):
                if rec.merged:
                    await _delivery.close_out(ctx, rec)
                raise
        if rec.merged and not rec.delivery_rejected:
            await _delivery.finish(ctx, rec)
    finally:
        tx.spend_pin = reservation.spend_pin = None


async def settle(tx: Transaction, error: BaseException) -> None:
    ctx, rec = tx.ctx, tx.reservation.wait.rec
    try:
        async with ctx.workspace.git_lease():
            if tx.guard is not None and rec.evidence is not tx.definitive:
                # An authorization query may detect moved HEAD before the final barrier.
                # Latch metadata damage even then; an authorized, landed commit is recovery's.
                with suppress(IntegrityError):
                    await tx.metadata()
            if tx.tree is None:
                tx.tree = (
                    await ctx.git(ctx.workspace.main, "rev-parse", f"{tx.pre}^{{tree}}")
                ).strip()
            facts = await _recovery.read_facts(ctx, tx.reservation.evidence.head_commit)
            if facts.head != tx.pre:
                if tx.guard is None or rec.evidence is not tx.definitive:
                    raise RulesFailure(
                        "main_suspect", "Main moved without a verified rules commit."
                    )
                await _recovery.verify_rules_merge(ctx, rec, tx.guard)
                await tx.record(facts.head)
                return  # A landed effect never produces a late error or a second merge.
            if facts.merging:
                await _recovery.abort_rules(ctx, rec, tx.pre, tx.tree)
            else:
                await _recovery.verify_rules_abort(ctx, rec, tx.pre, tx.tree)
        rec.fallback = "ready"
        if isinstance(error, CandidateVeto):
            return
        code = (
            "delivery_failed"
            if isinstance(error, EventRejected)
            else getattr(error, "code", "git_failed")
        )
        if isinstance(error, asyncio.CancelledError):
            code = "cancelled"
        if isinstance(error, IsolationUnavailable) or (
            isinstance(error, SuiteUnavailable) and isinstance(error.cause, IsolationUnavailable)
        ):
            code = "isolation_unavailable"
    except Exception as recovery_error:
        ctx.state.suspect = True
        rec.fallback = "broken"
        code, error = "main_suspect", recovery_error
    await _rules.terminal(ctx, tx.reservation, code, f"Rules merge stopped: {error or code}")
