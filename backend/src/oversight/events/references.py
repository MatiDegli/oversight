"""Validate log references against committed or earlier accepted envelopes only."""

from collections.abc import Callable, Mapping
from typing import Any, cast

from oversight.events.lead_wake import is_lead_wake_trigger
from oversight.events.preview_resolution import validate_preview_status, validate_preview_view
from oversight_contracts.events import ServerEvent


class EventRejected(ValueError):
    """A semantic rejection of one event; retrying it cannot succeed."""


def validate_cross_review_limit(event: ServerEvent, prior: Mapping[int, ServerEvent]) -> None:
    """One automatic revision per change and per review, across all proposal revisions."""
    if event.type != "change.resolved" or event.payload.decided_by != "cross_review":
        return
    resolution = event.payload
    for earlier in prior.values():
        if earlier.type == "change.resolved" and earlier.payload.decided_by == "cross_review":
            if earlier.payload.change_id == resolution.change_id:
                raise ValueError("a change may have only one cross_review resolution")
            if earlier.payload.review_seq == resolution.review_seq:
                raise ValueError("a review_seq may have only one cross_review resolution")


def validate_references(
    event: ServerEvent,
    prior: Mapping[int, ServerEvent],
    committed: Mapping[int, ServerEvent] | None = None,
) -> None:
    """References cannot see future or rejected candidates, including within one SQL batch."""

    if event.type == "preview.status":
        validate_preview_status(event.payload, prior)
        return

    if event.type == "lead.view":
        # The store passes only the index committed before this SQL batch. No prior scan/fold.
        source = prior if committed is None else committed
        view = event.payload
        if view.through_seq >= event.seq or view.through_seq > len(source):
            raise ValueError("view horizon must be committed and earlier")
        if view.view.kind == "moment" and (
            view.view.event_seq > view.through_seq or source.get(view.view.event_seq) is None
        ):
            raise ValueError("view moment must belong to the observed committed prefix")
        if view.view.kind == "preview":
            validate_preview_view(
                view.view.agent_id, source, view.through_seq, view.agent_id, prior
            )
        return

    def referenced(seq: int, expected: str | None = None) -> ServerEvent:
        if seq not in prior:
            raise ValueError("event reference is absent or not earlier")
        source = prior[seq]
        if expected is not None and source.type != expected:
            raise ValueError(f"event reference requires {expected}")
        return source

    _validate_reply(event, prior, referenced)
    _walk_references(event.payload.model_dump(mode="json"), referenced)
    if event.type == "change.resolved":
        for use in event.payload.grants or ():
            auth = use.authorization
            source = referenced(auth.creation_seq, "grant.created")
            assert source.type == "grant.created"
            created = source.payload
            if (
                use.grant_id,
                auth.reason,
                auth.scope,
                auth.run_id,
                auth.session_id,
                auth.creation_ts,
            ) != (
                created.grant_id,
                created.reason,
                created.scope,
                created.run_id,
                created.session_id,
                source.ts,
            ):
                raise ValueError("grant authorization differs from its creation receipt")
    validate_cross_review_limit(event, prior)
    if event.type == "change.resolved" and event.payload.review_seq is not None:
        source = referenced(event.payload.review_seq, "change.reviewed")
        assert source.type == "change.reviewed"
        resolution, review = event.payload, source.payload
        if (resolution.change_id, resolution.revision, resolution.head_commit) != (
            review.change_id,
            review.revision,
            review.head_commit,
        ):
            raise ValueError("resolution review target differs")
        if review.verdict != "changes_requested":
            raise ValueError("revision request requires an adverse review")


def _validate_reply(
    event: ServerEvent,
    prior: Mapping[int, ServerEvent],
    referenced: Callable[[int, str | None], ServerEvent],
) -> None:
    if event.type == "lead.reply":
        reply = event.payload
        if reply.answers_seq is not None:
            if reply.answers_seq >= event.seq:
                raise ValueError("answers_seq must be earlier than the reply")
            source = referenced(reply.answers_seq, "agent.message")
            assert source.type == "agent.message"
            if source.payload.agent_id != reply.agent_id:
                raise ValueError("answers_seq must address the same lead")
        if reply.woken_by is not None:
            earlier_replies = [
                row.payload
                for row in prior.values()
                if row.seq < event.seq and row.type == "lead.reply"
            ]
            if not any(row.agent_id == reply.agent_id for row in earlier_replies):
                raise ValueError("the first lead reply cannot have woken_by")
            consumed = {seq for row in earlier_replies for seq in row.woken_by or ()}
            for seq in reply.woken_by:
                if seq >= event.seq:
                    raise ValueError("woken_by must be earlier than the reply")
                if not is_lead_wake_trigger(prior, seq, reply.agent_id):
                    raise ValueError("woken_by must name a catalog trigger of the same lead")
                if seq in consumed:
                    raise ValueError("woken_by event was already named by an earlier reply")
        if reply.through_seq is not None and reply.through_seq >= event.seq:
            raise ValueError("through_seq must be lower than the reply seq")


def _walk_references(value: Any, referenced: Callable[[int, str | None], ServerEvent]) -> None:
    if isinstance(value, dict):
        for key, item in cast(dict[str, Any], value).items():
            if item is not None and key in {
                "event_seq",
                "review_seq",
                "resolution_seq",
                "before_seq",
                "after_seq",
                "failed_seq",
                "edited_seq",
                "passed_seq",
            }:
                expected = {
                    "review_seq": "change.reviewed",
                    "resolution_seq": "change.resolved",
                    "before_seq": "tests.executed",
                    "after_seq": "tests.executed",
                    "failed_seq": "tests.executed",
                    "passed_seq": "tests.executed",
                    "edited_seq": "change.updated",
                }.get(key)
                referenced(item, expected)
            elif key == "test_runs":
                for seq in cast(list[int], item):
                    referenced(seq, "tests.executed")
            else:
                _walk_references(item, referenced)
    elif isinstance(value, list):
        for item in cast(list[Any], value):
            _walk_references(item, referenced)
