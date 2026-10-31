"""One-shot rules authorization; provisional evidence never authorizes a Git effect."""

from collections.abc import Sequence
from dataclasses import dataclass

from oversight.changes import _spend
from oversight.changes._assessment import PROFILE, AssessmentWait
from oversight.changes._decision_validation import validate_decision_target
from oversight.changes._evidence import WriteEvidence
from oversight.changes._policy import PolicyInputs, assess
from oversight.changes._state import APPROVABLE, WrongState
from oversight.changes._support import ChangeContext, RulesFailure
from oversight.events import StoredEvent
from oversight_contracts.grants import GrantUse, covered_rule
from oversight_contracts.policy import POLICY_RULES, ChangeAssessment


@dataclass
class RulesReservation:
    wait: AssessmentWait
    evidence: WriteEvidence
    decision: int
    spend_pin: StoredEvent | None = None


def pending_policy(assessment: ChangeAssessment, grants: Sequence[GrantUse] = ()) -> bool:
    return (
        assessment.policy_version == PROFILE
        and assessment.mode == "merge_when_safe"
        and len(assessment.rules) == len(POLICY_RULES[PROFILE])
        and {rule.rule for rule in assessment.rules} == set(POLICY_RULES[PROFILE])
        and len(grants) == len({use.rule for use in grants})
        and all(
            any(
                rule.rule == use.rule and covered_rule(assessment, rule, grants)
                for rule in assessment.rules
            )
            for use in grants
        )
        and all(
            (rule.result == "unknown" and rule.observed is None)
            if rule.rule == "combined_tests"
            else (rule.result == "pass" and bool(rule.evidence))
            or covered_rule(assessment, rule, grants)
            for rule in assessment.rules
        )
    )


def paths_confirmed(ctx: ChangeContext, wait: AssessmentWait, assessment: ChangeAssessment) -> bool:
    """A cached pass cannot outlive or replace either original protected-path source."""
    paths = ctx.assessment.path_sources(wait)
    if paths is None:
        return False
    expected = assess(
        PolicyInputs(
            change_id=assessment.change_id,
            agent_id=assessment.agent_id,
            revision=assessment.revision,
            base_commit=assessment.base_commit,
            head_commit=assessment.head_commit,
            main_commit=assessment.main_commit,
            mode=assessment.mode,
            mode_revision=assessment.mode_revision,
            protected_paths=paths[0],
            changed_files=paths[1],
        ),
        PROFILE,
    )
    rule = next(r for r in expected.rules if r.rule == "protected_paths")
    return rule.result == "pass" and rule in assessment.rules


def capture(
    ctx: ChangeContext, wait: AssessmentWait, assessment: ChangeAssessment
) -> RulesReservation:
    rec = wait.rec
    grants = (
        ctx.grants.uses(ctx, assessment, wait.grant_measurement)
        if wait.grant_measurement is not None
        else ()
    )
    if failure := availability(ctx):
        raise RulesFailure(*failure)
    ledger = ctx.ports.spend_ledger
    if ledger is not None and ledger.state is None:
        raise _spend.SpendNotReady
    _spend.guard(ctx, ctx.assessment.inputs(wait))
    if (
        not pending_policy(assessment, grants)
        or not paths_confirmed(ctx, wait, assessment)
        or assessment != wait.latest
        or any(
            not ctx.assessment.admits(rule.rule, ref, wait.spend_pin)
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
        grants=grants,
    )
    return RulesReservation(wait, evidence, rec.decision + 1, wait.spend_pin)


def current(ctx: ChangeContext, reservation: RulesReservation, *, integrity: bool = False) -> bool:
    """Suppress callbacks after a person, another revision, terminal acknowledgment or close."""
    wait, rec = reservation.wait, reservation.wait.rec
    return (
        (not ctx.state.closing or (integrity and rec.person_intent is not None))
        and ctx.assessment.waits.get(rec.change_id) is wait
        and (rec.person_intent is None or integrity)
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
    try:
        _spend.guard(ctx, ctx.assessment.inputs(wait))
    except RulesFailure as error:
        return error.code, str(error)
    rec, assessment = wait.rec, evidence.assessment
    grants = (
        ctx.grants.uses(ctx, assessment, wait.grant_measurement, only=evidence.grants)
        if assessment is not None and wait.grant_measurement is not None
        else ()
    )
    if evidence.grants and {g.grant_id for g in evidence.grants} != {g.grant_id for g in grants}:
        return (
            "policy_invalid",
            "A standing grant is no longer active or no longer covers this wait.",
        )
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
        or not pending_policy(assessment, grants)
        or not paths_confirmed(ctx, wait, assessment)
        or any(
            not ctx.assessment.admits(rule.rule, ref, reservation.spend_pin)
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
    capability = ctx.mode.capability()
    failure = None
    if not ctx.workspace.isolation.available:
        failure = "isolation_unavailable", "Network isolation C is unavailable for this session"
    elif ctx.workspace.integrity.rules_suspended:
        failure = "policy_invalid", "Rules authority is suspended until restart"
    elif not capability.confirmed:
        failure = "policy_invalid", capability.reason
    if failure:
        ctx.mode.report_loss(ctx, failure[1])
        return failure
    return None


async def terminal(
    ctx: ChangeContext, reservation: RulesReservation, code: str, reason: str
) -> None:
    availability(ctx)
    if code == "worktree_unverifiable":
        if reason.startswith("Rules preflight failed: code=integrity_invalid; reason="):
            code = "integrity_invalid"
        elif "output was truncated" in reason:
            code = "git_failed"  # Preserve preflight's known failures through its generic bucket.
    await ctx.assessment.report_terminal(
        reservation.wait,
        code,
        reason,
        lambda: current(ctx, reservation, integrity=code in {"main_suspect", "integrity_invalid"}),
    )
