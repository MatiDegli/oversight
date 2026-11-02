"""A pytest plugin that appends one flushed JSON line per report to $OVERSIGHT_REPORT.

It runs inside the demo repo's own environment, so it imports nothing from Oversight. The runner
reads the file after the run, which works even when pytest was killed halfway: every line is
already on disk.
"""

import importlib.util
import json
import ntpath
import os
import sys
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


def _within(root: str, path: str) -> bool:
    root, path = (ntpath.normcase(os.path.realpath(p)) for p in (root, path))
    try:
        return ntpath.commonpath([root, path]) == root
    except ValueError:
        return False


def import_problem() -> str | None:
    """Child-only check; validated venv libraries may be inside the original project."""
    raw = os.environ.get("OVERSIGHT_IMPORT_GUARD")
    if raw is None:
        return None
    try:
        policy = json.loads(raw)
        trusted = os.environ["OVERSIGHT_TRUSTED_PLUGIN"]
        if (
            os.path.normcase(os.path.realpath(__file__)) != os.path.normcase(trusted)
            or __spec__ is None
            or __spec__.origin is None
            or os.path.normcase(os.path.realpath(__spec__.origin)) != os.path.normcase(trusted)
        ):
            return "trusted report plugin shadowed"
        for name in policy["packages"]:
            spec = importlib.util.find_spec(name)
            locations = (
                [] if spec is None or spec.origin in (None, "built-in", "frozen") else [spec.origin]
            )
            locations += list(spec.submodule_search_locations or ()) if spec else []
            if not locations or any(not _within(policy["tree"], p) for p in locations):
                return "mapped package outside tested tree"
        for module in tuple(sys.modules.values()):
            spec = getattr(module, "__spec__", None)
            locations = [getattr(module, "__file__", None), getattr(spec, "origin", None)]
            locations += list(getattr(module, "__path__", ()))
            locations += list(getattr(spec, "submodule_search_locations", ()) or ())
            for path in locations:
                if (
                    path
                    and path not in ("built-in", "frozen")
                    and _within(policy["source"], path)
                    and not any(_within(lib, path) for lib in policy["libraries"])
                ):
                    return "loaded module reaches original project"
    except Exception:
        return "import guard could not verify origins"
    return None


def guard_imports(*, success: bool = False) -> None:
    if reason := import_problem():
        _append({"kind": "guard", "ok": False, "reason": reason})
        pytest.exit("Own-source import guard refused", returncode=4)
    if success and "OVERSIGHT_IMPORT_GUARD" in os.environ:
        _append({"kind": "guard", "ok": True})


@pytest.hookimpl(trylast=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    guard_imports()


@pytest.hookimpl(trylast=True)
def pytest_collection_finish(session: pytest.Session) -> None:
    guard_imports(success=True)


def pytest_configure(config: pytest.Config) -> None:
    # Create the file at once: a missing file then means the plugin never ran.
    _append({"kind": "start"})
    global _recorder
    _recorder = configure()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    guard_imports()
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
    try:
        guard_imports()
    finally:
        if _recorder is not None:
            _recorder.finish()


def prepare_imports() -> None:
    """Once at plugin import, remove original editable namespace paths from child startup.

    Later conftest/test insertions are checked, never repaired. Finders for dependencies stay.
    """
    if raw := os.environ.get("OVERSIGHT_IMPORT_GUARD"):
        policy = json.loads(raw)

        def original(path: str) -> bool:
            return _within(policy["source"], path) and not any(
                _within(lib, path) for lib in policy["libraries"]
            )

        sys.path[:] = [path for path in sys.path if not original(path)]
        for name, module in tuple(sys.modules.items()):
            if not name.startswith("__editable__"):
                continue
            mapping: Any = getattr(module, "MAPPING", {})
            namespaces: Any = getattr(module, "NAMESPACES", {})
            targets = list(mapping.values()) + [p for paths in namespaces.values() for p in paths]
            if any(original(path) for path in targets):
                placeholder = getattr(module, "PATH_PLACEHOLDER", None)
                sys.path[:] = [path for path in sys.path if path != placeholder]


prepare_imports()
