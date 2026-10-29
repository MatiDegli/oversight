"""A pytest plugin that appends one flushed JSON line per report to $OVERSIGHT_REPORT.

It runs inside the demo repo's own environment, so it imports nothing from Oversight. The runner
reads the file after the run, which works even when pytest was killed halfway: every line is
already on disk.
"""

import json
import os
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from .oversight_calls import Recorder, configure
else:
    from oversight_calls import Recorder, configure

_recorder: Recorder | None = None


def _append(record: dict[str, object]) -> None:
    path = os.environ.get("OVERSIGHT_REPORT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")


def pytest_configure(config: pytest.Config) -> None:
    # Create the file at once: a missing file then means the plugin never ran.
    _append({"kind": "start"})
    global _recorder
    _recorder = configure()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    _append(
        {"kind": "test", "nodeid": report.nodeid, "when": report.when, "outcome": report.outcome}
    )


def pytest_collectreport(report: pytest.CollectReport) -> None:
    if report.failed:
        _append({"kind": "collect", "nodeid": report.nodeid, "outcome": "failed"})


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, Any, Any]:
    if _recorder is not None:
        _recorder.test_id = item.nodeid
    try:
        return (yield)
    finally:
        if _recorder is not None:
            _recorder.test_id = None


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _recorder is not None:
        _recorder.finish()
