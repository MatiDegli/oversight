"""Regressions for the shortcut detector's final review."""

import pytest

from oversight.changes import ShortcutKind, detect_shortcuts


@pytest.mark.parametrize("removed_first", [False, True])
def test_numeric_rewording_keeps_independent_removed_check(removed_first: bool) -> None:
    checks = [
        "    assert 'limit 3' in result.text\n",
        "    assert 'access denied' in result.text\n",
    ]
    if removed_first:
        checks.reverse()
    old = "def test_status():\n" + "".join(checks)
    new = "def test_status():\n    assert 'limit: 3' in result.text\n"
    findings = detect_shortcuts([("test_app.py", old, new)])
    assert [(f.kind, f.line) for f in findings] == [
        (ShortcutKind.ASSERTION_LOOSENED, 2 if removed_first else 3)
    ]


@pytest.mark.parametrize("signature", ["second", ""])
def test_local_rename_cannot_capture_existing_name(signature: str) -> None:
    old = f"def test_status({signature}):\n    first = run()\n    assert second == 73\n"
    new = old.replace("first =", "second =")
    assert [f.kind for f in detect_shortcuts([("test_app.py", old, new)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]


@pytest.mark.parametrize("prefix", ["def", "async def"])
@pytest.mark.parametrize("method", [False, True])
def test_existing_helper_assertions_are_compared(prefix: str, method: bool) -> None:
    old = f"{prefix} check_result():\n    assert run() == 73\n"
    if method:
        old = "class Checks:\n" + "".join("    " + line for line in old.splitlines(True))
    new = old.replace("assert run() == 73", "run()")
    assert [f.kind for f in detect_shortcuts([("tests/helpers.py", old, new)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_matched_test_parameter_tables_are_compared(move: bool, changed: bool) -> None:
    old = (
        "import pytest\n@pytest.mark.parametrize('value, expected', [(1, 73), (2, 99)])\n"
        "def test_result(value, expected):\n    assert run(value) == expected\n"
    )
    new = old.replace("test_result", "test_renamed")
    if changed:
        new = new.replace("(1, 73)", "(1, 74)")
    changes = [("test_old.py", old, new)]
    if move:
        changes = [("test_old.py", old, None), ("test_new.py", None, new)]
    findings = detect_shortcuts(changes)
    assert [f.kind for f in findings] == ([ShortcutKind.EXPECTED_VALUE_CHANGED] if changed else [])
    if changed:
        assert findings[0].path == ("test_new.py" if move else "test_old.py")
        assert "(74)" in findings[0].message


@pytest.mark.parametrize(
    "before, after, wider",
    [
        ("3, abs=1e-9", "3, abs=1e-6", True),
        ("-3, abs=1e-9", "-3, abs=1e-6", True),
        ("[3, -3], abs=1e-9", "[3, -3], abs=1e-6", True),
        ("{'x': 3}, abs=1e-9", "{'x': 3}, abs=1e-6", True),
        ("3, abs=1e-6", "3, abs=1e-9", False),
        ("3", "3, abs=1e-6", False),
        ("3, rel=1e-6, abs=1e-9", "3, rel=1e-6, abs=1e-6", False),
    ],
)
def test_abs_only_approx_disables_default_relative_tolerance(
    before: str, after: str, wider: bool
) -> None:
    old = f"import pytest\ndef test_result():\n    assert run() == pytest.approx({before})\n"
    new = old.replace(f"approx({before})", f"approx({after})")
    assert [f.kind for f in detect_shortcuts([("test_app.py", old, new)])] == (
        [ShortcutKind.ASSERTION_LOOSENED] if wider else []
    )
