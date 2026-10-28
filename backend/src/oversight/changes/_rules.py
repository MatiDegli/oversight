"""One-shot rules authorization; provisional evidence never authorizes a Git effect."""

from dataclasses import dataclass

from oversight.changes._assessment import PROFILE, AssessmentWait
from oversight.changes._decision_validation import validate_decision_target
from oversight.changes._evidence import WriteEvidence
from oversight.changes._state import APPROVABLE, WrongState
from oversight.changes._support import ChangeContext, RulesFailure
from oversight_contracts.policy import POLICY_RULES, ChangeAssessment


@dataclass(frozen=True)
class RulesReservation:
    wait: AssessmentWait
    evidence: WriteEvidence
    decision: int


def pending_policy(assessment: ChangeAssessment) -> bool:
    return (
        assessment.policy_version == PROFILE
        and assessment.mode == "merge_when_safe"
        and len(assessment.rules) == len(POLICY_RULES[PROFILE])
        and {rule.rule for rule in assessment.rules} == set(POLICY_RULES[PROFILE])
        and all(
            (rule.result == "unknown" and rule.observed is None)
            if rule.rule == "combined_tests"
            else rule.result == "pass" and bool(rule.evidence)
            for rule in assessment.rules
        )
    )


def capture(
    ctx: ChangeContext, wait: AssessmentWait, assessment: ChangeAssessment
) -> RulesReservation:
    rec = wait.rec
    if failure := availability(ctx):
        raise RulesFailure(*failure)
    if (
        not pending_policy(assessment)
        or assessment != wait.latest
        or any(
            ref.event_seq is not None and ref.event_seq not in ctx.assessment.events
            for rule in assessment.rules
            for ref in rule.evidence
        )
        or not assessment.candidate_tree
        or (
            assessment.change_id,
            assessment.agent_id,
            assessment.revision,
            assessment.base_commit,
            assessment.head_commit,
            assessment.main_commit,
            assessment.mode_revision,
        )
        != (
            rec.change_id,
            rec.agent_id,
            wait.target.revision,
            wait.proposal.base_commit,
            wait.target.head,
            wait.main_commit,
            wait.mode.revision,
        )
    ):
        raise ValueError("Rules require current evidence for the exact pending policy and target.")
    validate_decision_target(rec, assessment.revision, assessment.head_commit, APPROVABLE)
    evidence = WriteEvidence(
        rec.change_id,
        rec.agent_id,
        rec.revision,
        rec.base_commit,
        assessment.head_commit,
        "rules",
        "approved",
        None,
        assessment=assessment.model_copy(deep=True),
        reasons=("All prerequisites confirmed; combined tests pending.",),
    )
    return RulesReservation(wait, evidence, rec.decision + 1)


def current(ctx: ChangeContext, reservation: RulesReservation) -> bool:
    """Suppress callbacks after a person, another revision, terminal acknowledgment or close."""
    wait, rec = reservation.wait, reservation.wait.rec
    return (
        not ctx.state.closing
        and ctx.assessment.waits.get(rec.change_id) is wait
        and rec.state == "deciding"
        and rec.decision == reservation.decision
        and rec.resolution is None
        and wait.terminal_seq is None
        and (rec.revision, rec.head_commit) == (wait.target.revision, wait.target.head)
    )


def revalidate(ctx: ChangeContext, reservation: RulesReservation) -> tuple[str, str] | None:
    """Read-only guards, repeated after asynchronous queries and before handing off."""
    wait, evidence = reservation.wait, reservation.evidence
    if failure := availability(ctx):
        return failure
    rec, assessment = wait.rec, evidence.assessment
    if (
        rec.taken != evidence.revision
        or evidence.revision not in rec.rules_consumed
        or rec.base_commit != evidence.base_commit
    ):
        return "target_changed", "Rules reservation no longer matches the proposed target."
    try:
        validate_decision_target(
            rec, evidence.revision, evidence.head_commit, APPROVABLE, reserved=True
        )
    except WrongState as error:
        return "target_changed", str(error)
    if (
        rec.evidence is not evidence
        or assessment is None
        or assessment != wait.latest
        or not pending_policy(assessment)
        or any(
            ref.event_seq is not None and ref.event_seq not in ctx.assessment.events
            for rule in assessment.rules
            for ref in rule.evidence
        )
        or rec.proposal_mode != wait.mode
        or wait.mode != ctx.mode.snapshot()
        or ctx.mode.authority_suspended()
        or ctx.state.suspect
        or ctx.workspace.integrity.main_suspect
    ):
        return "policy_invalid", "Rules evidence or merge authority is no longer confirmed."
    if ctx.state.snapshot.commit != assessment.main_commit:
        return "main_moved", "Main moved during rules evaluation; review this change yourself."
    return None


def availability(ctx: ChangeContext) -> tuple[str, str] | None:
    """Session facts only: restoration never clears workspace's suspension latch."""
    if not ctx.mode.capability().confirmed or not ctx.workspace.isolation.available:
        return "isolation_unavailable", "Confirmed session isolation is unavailable."
    if ctx.workspace.integrity.rules_suspended:
        return "policy_invalid", "Rules authority is suspended until restart."
    return None


async def terminal(
    ctx: ChangeContext, reservation: RulesReservation, code: str, reason: str
) -> None:
    if code == "worktree_unverifiable":
        if reason.startswith("Rules preflight failed: code=integrity_invalid; reason="):
            code = "integrity_invalid"
        elif "output was truncated" in reason:
            code = "git_failed"  # Preserve preflight's known failures through its generic bucket.
    await ctx.assessment.report_terminal(
        reservation.wait, code, reason, lambda: current(ctx, reservation)
    )
