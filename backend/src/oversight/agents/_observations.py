"""Compact new test observations before appending them to model history."""

from oversight.workspace import SuiteRun


def test_observation(run: SuiteRun, max_chars: int) -> str:
    result = run.result
    counts = f"{result.passed} passed, {result.failed} failed, {result.skipped} skipped"
    if result.outcome == "passed":
        return f"Tests passed: {counts}."
    failed = ", ".join(result.failed_tests) or "none reported"
    content = f"Suite outcome: {result.outcome}; {counts}\nFailed tests: {failed}"
    if len(run.output) > max_chars:
        content += f"\nOutput truncated: showing the last {max_chars} characters."
    return f"{content}\n\n{run.output[-max_chars:]}"
