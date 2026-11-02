"""Truncation notes describe the output that survived the cut."""

import pytest

from oversight.agents._observations import test_observation as observation
from oversight.workspace import SuiteRun
from oversight_contracts.events import SuiteResult


@pytest.mark.parametrize("harness_pack", [False, True])
def test_timeout_observation_preserves_failures_and_output(harness_pack: bool) -> None:
    run = SuiteRun(
        SuiteResult(
            outcome="timeout",
            passed=3,
            failed=2,
            skipped=1,
            duration_ms=75000,
            failed_tests=["tests/test_a.py::test_first"],
        ),
        "traceback",
    )
    assert observation(run, 1000, harness_pack=harness_pack, test_timeout_s=75) == (
        "Tests reached the 75 s limit. Observed: 3 passed, 2 failed, 1 skipped. "
        "Results are incomplete. The cause is unknown. "
        "Set a different test limit when starting the next run.\n"
        "Failed tests: tests/test_a.py::test_first\n\ntraceback"
    )


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
