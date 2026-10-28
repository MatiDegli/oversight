"""Compact new test observations before appending them to model history."""

from oversight.settings import harness_pack_enabled
from oversight.test_output import retain_failure
from oversight.workspace import SuiteRun


def test_observation(run: SuiteRun, max_chars: int) -> str:
    result = run.result
    counts = f"{result.passed} passed, {result.failed} failed, {result.skipped} skipped"
    if result.outcome == "passed":
        return f"Tests passed: {counts}."
    failed = ", ".join(result.failed_tests) or "none reported"
    content = f"Suite outcome: {result.outcome}; {counts}\nFailed tests: {failed}"
    if harness_pack_enabled():
        output = retain_failure(run.output, max_chars)
        if len(run.output) > max_chars:
            content += "\nOutput truncated: retaining the first failure and final tail."
        return f"{content}\n\n{output}"
    if len(run.output) > max_chars:
        content += f"\nOutput truncated: showing the last {max_chars} characters."
    return f"{content}\n\n{run.output[-max_chars:]}"
