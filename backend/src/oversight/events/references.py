"""Validate log references against committed or earlier accepted envelopes only."""

from collections.abc import Mapping
from typing import Any, cast

from oversight_contracts.events import ServerEvent


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


def validate_references(event: ServerEvent, prior: Mapping[int, ServerEvent]) -> None:
    """References cannot see future or rejected candidates, including within one SQL batch."""

    def referenced(seq: int, expected: str | None = None) -> ServerEvent:
        if seq not in prior:
            raise ValueError("event reference is absent or not earlier")
        source = prior[seq]
        if expected is not None and source.type != expected:
            raise ValueError(f"event reference requires {expected}")
        return source

    def walk(value: Any) -> None:
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
                    walk(item)
        elif isinstance(value, list):
            for item in cast(list[Any], value):
                walk(item)

    walk(event.payload.model_dump(mode="json"))
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
