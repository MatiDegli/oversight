"""A pytest plugin that appends one flushed JSON line per report to $OVERSIGHT_REPORT.

It runs inside the demo repo's own environment, so it imports nothing from Oversight. The runner
reads the file after the run, which works even when pytest was killed halfway: every line is
already on disk.
"""

import json
import os

import pytest


def _append(record: dict[str, object]) -> None:
    path = os.environ.get("OVERSIGHT_REPORT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")


def pytest_configure(config: pytest.Config) -> None:
    # Create the file at once: a missing file then means the plugin never ran.
    _append({"kind": "start"})


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _append(
        {"kind": "test", "nodeid": report.nodeid, "when": report.when, "outcome": report.outcome}
    )


def pytest_collectreport(report: pytest.CollectReport) -> None:
    if report.failed:
        _append({"kind": "collect", "nodeid": report.nodeid, "outcome": "failed"})
