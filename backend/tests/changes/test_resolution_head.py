"""Live head checks belong to the resolution producer, not the wire contract."""

import pytest

from oversight.changes._evidence import WriteEvidence
from oversight.changes._resolution_validation import validate_resolution_evidence
from oversight_contracts.events import ChangeResolved


@pytest.mark.parametrize("observed_head", ["", "head"])
def test_producer_rejects_missing_head_in_capture_or_payload(observed_head: str) -> None:
    evidence = WriteEvidence(
        change_id="change-1",
        agent_id="agent-1",
        revision=1,
        base_commit="base",
        head_commit=observed_head,
        actor="person",
        decision="rejected",
        comment=None,
    )
    payload = ChangeResolved(
        change_id="change-1",
        agent_id="agent-1",
        revision=1,
        decision="rejected",
        decided_by="person",
        head_commit=None,
        reason=None,
        comment=None,
        merge=None,
        merge_commit=None,
        conflict=None,
    )
    with pytest.raises(ValueError, match=r"observed head|captured authorization and target"):
        validate_resolution_evidence(payload, evidence)
