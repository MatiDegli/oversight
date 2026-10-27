"""Validate log references against committed or earlier accepted envelopes only."""

from typing import Any, cast

from oversight_contracts.events import ServerEvent


def validate_references(event: ServerEvent, prior: list[ServerEvent]) -> None:
    """References cannot see future or rejected candidates, including within one SQL batch."""

    def referenced(seq: int, expected: str | None = None) -> ServerEvent:
        if seq < 1 or seq > len(prior):
            raise ValueError("event reference is absent or not earlier")
        source = prior[seq - 1]
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
