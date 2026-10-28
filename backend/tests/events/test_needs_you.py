"""Shared golden for the backend owner and the future frontend recognizer."""

import json
from pathlib import Path

import pytest

from oversight.events import (
    NEEDS_YOU_CODES,
    NeedsYou,
    NeedsYouWait,
    format_needs_you,
    needs_you_golden,
    parse_needs_you,
)
from oversight_contracts.events import ErrorReport

GOLDEN = Path(__file__).resolve().parents[3] / "fixtures/events/needs-you.json"


def test_needs_you_golden_round_trip() -> None:
    golden = json.loads(GOLDEN.read_text())
    for key, value in needs_you_golden().items():
        assert golden[key] == value
    for case in golden["cases"]:
        parsed = parse_needs_you(case["message"])
        expected = NeedsYou(**case["parsed"]) if case["parsed"] else None
        assert parsed == expected
        if parsed:
            assert (
                format_needs_you(parsed.revision, parsed.head, parsed.code, parsed.reason)
                == case["message"]
            )
        assert (
            bool(
                parsed
                and (parsed.revision, parsed.head)
                == (case["current"]["revision"], case["current"]["head"])
            )
            == case["matches_current"]
        )
    for code in NEEDS_YOU_CODES:
        assert parse_needs_you(format_needs_you(0, "head", code, "Explain.")) == NeedsYou(
            0, "head", code, "Explain."
        )


@pytest.mark.parametrize(
    "revision,head,code,reason",
    [
        (1, "head", "untrusted", "Explain."),
        (-1, "head", "ledger", "Explain."),
        (1, "head; code=ledger", "ledger", "Explain."),
        (1, "head", "ledger", "  "),
    ],
)
def test_formatter_refuses_invalid_fields(revision: int, head: str, code: str, reason: str) -> None:
    with pytest.raises(ValueError):
        format_needs_you(revision, head, code, reason)


@pytest.mark.parametrize("code", [*NEEDS_YOU_CODES, "foreign"])
def test_rules_wait_requires_recognized_current_target_and_generation(code: str) -> None:
    wait = NeedsYouWait("change-1", 2, "head", 7)
    report = ErrorReport(
        source="server",
        agent_id=None,
        change_id="change-1",
        fatal=False,
        message=(
            f"Needs you — revision=2; head=head; code={code}; reason=Explain; code=ledger\nopaque"
        ),
    )
    assert wait.accepts(report, generation=7) == (code in NEEDS_YOU_CODES)
    assert not wait.accepts(report, generation=6)
    for patch in (
        {"change_id": "other"},
        {"message": report.message.replace("revision=2", "revision=1")},
        {"message": report.message.replace("head=head", "head=old")},
    ):
        assert not wait.accepts(report.model_copy(update=patch), generation=7)
