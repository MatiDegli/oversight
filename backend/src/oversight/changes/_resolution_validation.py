"""Pure authorization checks for intent and completed resolution evidence."""

from oversight.changes._evidence import WriteEvidence
from oversight.changes._policy import merges_alone
from oversight_contracts.events import ChangeResolved


def validate_resolution_intent(evidence: WriteEvidence) -> None:
    """Validate everything known before an effect, without inventing its Git result."""
    if evidence.revision == 0 and (
        evidence.decision != "rejected"
        or evidence.actor != "person"
        or evidence.head_commit != evidence.base_commit
    ):
        raise ValueError("revision zero requires a personal rejection of the agent base")
    if not evidence.head_commit:
        raise ValueError("a resolution requires an observed head")
    if evidence.grants and evidence.actor != "rules":
        raise ValueError("grant uses belong only to rules approvals")
    if evidence.actor == "rules" and (
        evidence.decision != "approved"
        or evidence.assessment is None
        or not merges_alone(evidence.assessment, evidence.grants)
        or not evidence.reasons
        or (
            evidence.grants
            and (evidence.change_id, evidence.agent_id, evidence.revision, evidence.head_commit)
            != (
                evidence.assessment.change_id,
                evidence.assessment.agent_id,
                evidence.assessment.revision,
                evidence.assessment.head_commit,
            )
        )
    ):
        raise ValueError("rules approval requires the enabled policy and reasons")
    if evidence.actor in {"cross_review", "lead_agent"}:
        if evidence.decision != "changes_requested" or not (evidence.comment or "").strip():
            raise ValueError("model actors may only request changes")
        if evidence.actor == "cross_review" and evidence.review_seq is None:
            raise ValueError("cross review requires its event")
        if evidence.actor == "lead_agent":
            status = evidence.agent_status
            if (
                status is None
                or evidence.requested_by not in evidence.registered_leads
                or status.started_by != evidence.requested_by
                or (status.agent_id, status.change_id) != (evidence.agent_id, evidence.change_id)
            ):
                raise ValueError("only the lead that started this coding agent may revise it")


def validate_resolution_evidence(payload: ChangeResolved, evidence: WriteEvidence) -> None:
    """Use the original authorization after effects, even after a base move or retry."""
    validate_resolution_intent(evidence)
    expected = (
        evidence.change_id,
        evidence.agent_id,
        evidence.revision,
        evidence.head_commit,
        evidence.actor,
        evidence.decision,
        evidence.comment,
        evidence.review_seq,
        evidence.requested_by,
    )
    actual = (
        payload.change_id,
        payload.agent_id,
        payload.revision,
        payload.head_commit,
        payload.decided_by,
        payload.decision,
        payload.comment,
        payload.review_seq,
        payload.requested_by,
    )
    if actual != expected:
        raise ValueError("resolution differs from its captured authorization and target")
    if payload.assessment != evidence.assessment or tuple(payload.reasons) != evidence.reasons:
        raise ValueError("resolution policy evidence differs from its capture")
    answers = tuple(payload.answers) if payload.answers is not None else None
    if answers != evidence.answers:
        raise ValueError("resolution answers differ from their captured authorization")
    if tuple(payload.grants or ()) != evidence.grants:
        raise ValueError("resolution grants differ from their captured authorization")
    ChangeResolved.model_validate(payload.model_dump(mode="json"), context={"live": True})
