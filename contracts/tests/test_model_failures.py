"""Server-owned failed attempts and malformed tool calls, approved for 1.8.0."""

import pytest
from pydantic import TypeAdapter, ValidationError

from oversight_contracts import events

ATTEMPT = dict(
    agent_id="agent-1",
    model="fake",
    provider="fake",
    attempt=1,
    max_attempts=5,
    cause="rate_limited",
    status=429,
    wait_s=2,
)
STEP = dict(
    agent_id="agent-1",
    step=1,
    tool="unknown",
    summary="Malformed call",
    result="error",
    error="Unknown tool",
    duration_ms=0,
    tests=None,
)


@pytest.mark.parametrize(
    "cause,status",
    [
        ("rate_limited", 429),
        ("server_error", 500),
        ("server_error", 599),
        ("refused", 400),
        ("refused", 403),
        ("connection", None),
        ("timeout", None),
    ],
)
def test_each_failure_cause_round_trips(cause, status):
    payload = events.ModelAttemptFailed.model_validate(
        {**ATTEMPT, "cause": cause, "status": status}
    )
    adapter = TypeAdapter(events.ServerEvent)
    event = adapter.validate_python(
        dict(
            seq=1,
            ts="2026-10-30T14:00:00.000Z",
            type="model.attempt_failed",
            payload=payload,
        )
    )
    assert adapter.validate_json(adapter.dump_json(event)) == event


@pytest.mark.parametrize(
    "cause,status",
    [
        ("rate_limited", None),
        ("rate_limited", 400),
        ("rate_limited", 500),
        ("server_error", None),
        ("server_error", 499),
        ("server_error", 600),
        ("refused", None),
        ("refused", 429),
        ("refused", 500),
        ("refused", 599),
        ("connection", 400),
        ("timeout", 504),
    ],
)
def test_failure_status_matches_cause(cause, status):
    with pytest.raises(ValidationError):
        events.ModelAttemptFailed.model_validate({**ATTEMPT, "cause": cause, "status": status})


@pytest.mark.parametrize(
    "update",
    [
        dict(wait_s=None),
        dict(final="deadline"),
        dict(attempt=6),
        dict(attempt=0),
        dict(max_attempts=0),
        dict(wait_s=-1),
        dict(retry_after_s=-1),
        dict(wait_s=None, final="attempts_used"),
    ],
)
def test_attempt_and_final_invariants(update):
    with pytest.raises(ValidationError):
        events.ModelAttemptFailed.model_validate({**ATTEMPT, **update})


@pytest.mark.parametrize("final", ["attempts_used", "not_retryable", "deadline"])
def test_final_failure_has_no_wait(final):
    parsed = events.ModelAttemptFailed.model_validate(
        {
            **ATTEMPT,
            "attempt": 5,
            "wait_s": None,
            "final": final,
        }
    )
    assert parsed.final == final and parsed.wait_s is None


@pytest.mark.parametrize("kind", ["unknown_tool", "invalid_json", "invalid_arguments"])
def test_malformed_tool_kind_requires_error(kind):
    parsed = events.AgentStep.model_validate({**STEP, "error_kind": kind})
    assert parsed.error_kind == kind
    with pytest.raises(ValidationError):
        events.AgentStep.model_validate({**STEP, "error_kind": kind, "result": "ok"})


def test_older_steps_default_to_null():
    assert events.AgentStep.model_validate(STEP).error_kind is None
