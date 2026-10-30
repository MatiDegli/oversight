"""Standing grants keep historical wire records and exact authorization captures."""

import pytest
from pydantic import TypeAdapter

from oversight_contracts.commands import ApproveChange
from oversight_contracts.events import ServerEvent
from oversight_contracts.grants import GrantCreated, GrantRequest, GrantRevoked, GrantScope

TS = "2026-10-29T14:00:00.000Z"
RUN = "run-20261029-140000"


@pytest.mark.parametrize("kind", ["module", "path", "agent", "run"])
@pytest.mark.parametrize("reason", ["possible_callers", "protected_path"])
@pytest.mark.parametrize("expires", ["run", "session"])
def test_request_round_trip(kind, reason, expires):
    request = GrantRequest(
        reason=reason, scope=GrantScope(kind=kind, value="shop.pricing", expires=expires)
    )
    approve = ApproveChange(revision=1, head_commit="abc", grant=request)
    assert ApproveChange.model_validate_json(approve.model_dump_json()) == approve


def test_old_approve_and_grant_envelopes():
    assert ApproveChange(revision=1, head_commit="abc").grant is None
    created = GrantCreated(
        grant_id="grant-1",
        reason="possible_callers",
        scope=GrantScope(kind="module", value="shop.pricing", expires="run"),
        change_id="change-1",
        revision=1,
        head_commit="abc",
        run_id=RUN,
        granted_by="person",
    )
    for seq, kind, payload in (
        (1, "grant.created", created),
        (2, "grant.revoked", GrantRevoked(grant_id="grant-1")),
    ):
        event = TypeAdapter(ServerEvent).validate_python(
            dict(seq=seq, ts=TS, type=kind, payload=payload)
        )
        assert TypeAdapter(ServerEvent).validate_json(event.model_dump_json()) == event
