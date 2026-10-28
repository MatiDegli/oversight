"""Bound imported helper analysis and distinguish absent helpers from opaque calls."""

import sys

import pytest

from oversight.changes import ShortcutKind, detect_shortcuts
from oversight.changes._shortcuts_common import Source
from oversight.changes._shortcuts_helpers import _imported_calls, _imported_target, _module_index

OLD = "def check():\n    assert f() == 3\ndef test_f():\n    check()\n"


@pytest.mark.parametrize(
    "statement,call,name",
    [
        ("import subprocess", "subprocess.run([])", "run"),
        ("import os.path", "os.path.join('a', 'b')", "join"),
    ],
)
def test_defined_helper_does_not_make_unrelated_import_unknown(
    statement: str, call: str, name: str
) -> None:
    old = (
        f"{statement}\ndef {name}():\n    assert f() == 3\n"
        f"def test_f():\n    {name}()\n    {call}\n"
    )
    result = detect_shortcuts([("tests/test_calc.py", old, old + "def test_g():\n    pass\n")])
    assert result.status == "no_facts" and not result


@pytest.mark.parametrize("init", ["", "from .helpers import other\n"])
def test_package_without_the_function_is_not_an_import_candidate(init: str) -> None:
    result = detect_shortcuts(
        [
            ("tests/test_calc.py", OLD, "from .helpers import check\ndef test_f():\n    check()\n"),
            ("tests/helpers.py", None, "def check():\n    assert f() == 3\n"),
            ("tests/__init__.py", "", init),
        ]
    )
    assert result.status == "no_facts" and not result


def test_visible_reexport_is_unknown_instead_of_call_removed() -> None:
    result = detect_shortcuts(
        [
            ("tests/test_calc.py", OLD, "from helpers import check\ndef test_f():\n    check()\n"),
            ("tests/helpers.py", None, "from assertions import check\n"),
        ]
    )
    assert result.status == "unknown" and result.reasons
    assert {f.kind for f in result} == {ShortcutKind.UNKNOWN}
    assert "target is not visible" in " ".join(result.reasons)


@pytest.mark.parametrize("validation", ["model_validate", "model_validate_json"])
@pytest.mark.parametrize(
    "new", ["def test_f():\n    pass\n", "def test_f():\n    Event.VALIDATE(RAW)\n"]
)
def test_validation_alone_is_not_an_asserting_helper(validation: str, new: str) -> None:
    old = f"def event():\n    Event.{validation}(RAW)\ndef test_f():\n    event()\n"
    result = detect_shortcuts([("tests/test_calc.py", old, new.replace("VALIDATE", validation))])
    assert result.status == "no_facts" and not result


def test_validation_does_not_replace_an_unrelated_asserting_helper() -> None:
    result = detect_shortcuts(
        [
            ("tests/test_calc.py", OLD, "from helpers import check\ndef test_f():\n    check()\n"),
            ("tests/helpers.py", None, "def check():\n    Event.model_validate(RAW)\n"),
        ]
    )
    assert result.status == "findings"
    assert {f.kind for f in result} == {ShortcutKind.ASSERTION_LOOSENED}


def test_twenty_thousand_imported_calls_over_four_hundred_files_are_bounded() -> None:
    # Count executed Python lines in helper analysis, including comprehension frames.
    # A per-call scan of 400 files exceeds this linear budget regardless of CPU speed.
    from oversight.changes import _shortcuts_helpers as helpers

    calls = "    check()\n" * 20_000
    source = Source("from helpers import check\ndef test_f():\n" + calls)
    sources = {"tests/test_calc.py": source} | {
        f"pkg/module_{i}.py": Source("VALUE = 1\n") for i in range(399)
    }
    work = 0

    def trace(frame, event, arg):
        nonlocal work
        if frame.f_code.co_filename != helpers.__file__:
            return None
        if event == "line":
            work += 1
        return trace

    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        index = _module_index(sources, deadline=float("inf"))
        visible, opaque = _imported_calls(
            source.tests["test_f"], source, {"check": "check"}, "tests/test_calc.py", index
        )
    finally:
        sys.settrace(previous)
    assert not visible and opaque == {"check": 20_000}
    assert work <= 100 * 20_000 + 20 * 400, work


def test_module_index_is_built_once_per_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    builds = 0

    def build(sources: dict[str, Source], deadline: float) -> dict[str, list[Source]]:
        nonlocal builds
        builds += 1
        return _module_index(sources, deadline)

    monkeypatch.setattr("oversight.changes._shortcuts_helpers._module_index", build)
    result = detect_shortcuts(
        [
            (
                "tests/test_calc.py",
                OLD + "def test_g():\n    check()\n",
                "from helpers import check\ndef test_f():\n    check()\n    check()\n"
                "def test_g():\n    check()\n",
            ),
            ("tests/helpers.py", None, "def check():\n    assert f() == 3\n"),
        ]
    )
    assert builds == 1
    assert result.status == "no_facts"


def test_helper_calls_cross_deadline_inside_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Source("def test_f():\n    local()\n    local()\n", deadline=3)
    ticks = iter(range(20))
    monkeypatch.setattr("oversight.changes._shortcuts_common.time.monotonic", ticks.__next__)
    with pytest.raises(TimeoutError, match="analysis timeout"):
        _imported_calls(source.tests["test_f"], source, {}, "test_calc.py", {}, {})


def test_module_index_crosses_deadline_inside_build(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Source("")
    ticks = iter(range(20))
    monkeypatch.setattr("oversight.changes._shortcuts_common.time.monotonic", ticks.__next__)
    with pytest.raises(TimeoutError, match="analysis timeout"):
        _module_index({f"pkg/module_{i}.py": source for i in range(8)}, deadline=3)


@pytest.mark.parametrize("shape", ["splits", "candidates"])
def test_import_lookup_crosses_deadline_inside_index(
    monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    source = Source("")
    resolved = "a.b.c.d.e.check" if shape == "splits" else "a.check"
    index = {} if shape == "splits" else {"a": [source] * 8}
    ticks = iter(range(20))
    monkeypatch.setattr("oversight.changes._shortcuts_common.time.monotonic", ticks.__next__)
    with pytest.raises(TimeoutError, match="analysis timeout"):
        _imported_target(resolved, "tests/test_calc.py", index, deadline=3)
