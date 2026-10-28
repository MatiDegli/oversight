"""Shared golden for the backend owner and the future frontend recognizer."""

import json
from pathlib import Path

import pytest

from oversight.events import NEEDS_YOU_CODES, NeedsYou, format_needs_you, parse_needs_you

GOLDEN = Path(__file__).resolve().parents[3] / "fixtures/events/needs-you.json"


def test_needs_you_golden_round_trip() -> None:
    golden = json.loads(GOLDEN.read_text())
    assert golden["codes"] == list(NEEDS_YOU_CODES)
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
