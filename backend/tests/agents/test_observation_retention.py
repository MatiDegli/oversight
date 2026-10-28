"""Truncation notes describe the output that survived the cut."""

import pytest

from oversight.agents._observations import test_observation as observation
from oversight.workspace import SuiteRun
from oversight_contracts.events import SuiteResult


@pytest.mark.parametrize("failure_header", [False, True])
def test_note_only_claims_a_first_failure_when_retained(failure_header: bool) -> None:
    first = (
        "___ test_first ___\nAssertionError: first\n" if failure_header else "ImportError: setup\n"
    )
    output = first + "x" * 5000 + "\nfinal tail"
    run = SuiteRun(
        SuiteResult(outcome="error", passed=0, failed=1, skipped=0, duration_ms=1, failed_tests=[]),
        output,
    )
    text = observation(run, 1000, harness_pack=True)
    if failure_header:
        assert "retaining the first failure and final tail" in text
        assert "AssertionError: first" in text
    else:
        assert "showing the last 1000 characters" in text
        assert "retaining the first failure" not in text
        assert text.endswith(output[-1000:])
