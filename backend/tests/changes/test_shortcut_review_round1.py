"""Regressions for the first fix round after the detector's final review."""

import pytest

from oversight.changes import ShortcutKind, detect_shortcuts


@pytest.mark.parametrize("removed", ["access denied", "access denied 4"])
@pytest.mark.parametrize("removed_first", [False, True])
def test_rewording_cannot_cancel_a_check_through_a_shared_word(
    removed: str, removed_first: bool
) -> None:
    checks = [
        "    assert 'access limit 3' in result.text\n",
        f"    assert {removed!r} in result.text\n",
    ]
    if removed_first:
        checks.reverse()
    old = "def test_status():\n" + "".join(checks)
    new = "def test_status():\n    assert 'access limit: 3' in result.text\n"
    findings = detect_shortcuts([("test_app.py", old, new)])
    assert [(f.kind, f.line) for f in findings] == [
        (ShortcutKind.ASSERTION_LOOSENED, 2 if removed_first else 3)
    ]


@pytest.mark.parametrize(
    "setup",
    [
        "first: int = run()\n",
        "(first := run())\n",
        "for first in run():\n        use(first)\n",
        "for first, other in run():\n        use(first, other)\n",
        "async for first in run():\n        use(first)\n",
        "with run() as first:\n        use(first)\n",
        "with run() as (first, other):\n        use(first, other)\n",
        "async with run() as first:\n        use(first)\n",
    ],
)
@pytest.mark.parametrize("signature", ["", "second"])
def test_nonplain_assignment_rename_cannot_capture_a_checked_binding(
    setup: str, signature: str
) -> None:
    old = f"async def test_status({signature}):\n    {setup}    assert second == 73\n"
    new = old.replace("first", "second")
    findings = detect_shortcuts([("test_app.py", old, new)])
    assert [(f.kind, f.line) for f in findings] == [
        (ShortcutKind.ASSERTION_LOOSENED, len(old.splitlines()))
    ]
    assert detect_shortcuts([("test_app.py", old, old.replace("first", "renamed"))]) == []


def test_capture_is_detected_when_assignment_gains_an_annotation() -> None:
    old = "def test_status():\n    first = run()\n    assert second == 73\n"
    new = old.replace("first =", "second: int =")
    assert [f.kind for f in detect_shortcuts([("test_app.py", old, new)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]


@pytest.mark.parametrize(
    "replacement",
    [
        "second: int = run()\n",
        "(second := run())\n",
        "for second in run():\n        use(second)\n",
        "with run() as second:\n        use(second)\n",
    ],
)
def test_capture_cannot_overwrite_another_checked_local(replacement: str) -> None:
    old = "def test_status():\n    second = cached()\n    first = run()\n    assert second == 73\n"
    new = old.replace("first = run()\n", replacement)
    assert [f.kind for f in detect_shortcuts([("test_app.py", old, new)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]


@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("rename_class", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_matched_method_compares_its_class_parameter_table(
    move: bool, rename_class: bool, changed: bool
) -> None:
    old = (
        "import pytest\n@pytest.mark.parametrize('value, expected', [(1, 73), (2, 99)])\n"
        "class TestResult:\n"
        "    def test_result(self, value, expected):\n        assert run(value) == expected\n"
    )
    new = old.replace("test_result", "test_renamed")
    if rename_class:
        new = new.replace("TestResult", "TestRenamed")
    if changed:
        new = new.replace("(1, 73)", "(1, 74)")
    changes = [("test_old.py", old, new)]
    if move:
        changes = [("test_old.py", old, None), ("test_new.py", None, new)]
    findings = detect_shortcuts(changes)
    assert [f.kind for f in findings] == ([ShortcutKind.EXPECTED_VALUE_CHANGED] if changed else [])
    if changed:
        assert findings[0].path == ("test_new.py" if move else "test_old.py")
        assert findings[0].line == 2
        assert "(74)" in findings[0].message


def test_matched_method_compares_all_enclosing_class_tables() -> None:
    old = (
        "import pytest\n@pytest.mark.parametrize('value, expected', [(1, 73)])\n"
        "class TestOuter:\n"
        "    @pytest.mark.parametrize('other, expected_other', [(2, 99)])\n"
        "    class TestInner:\n"
        "        def test_result(self, value, expected, other, expected_other):\n"
        "            assert run(value, other) == (expected, expected_other)\n"
    )
    new = old.replace("test_result", "test_renamed").replace("TestOuter", "TestRenamed")
    new = new.replace("(1, 73)", "(1, 74)").replace("(2, 99)", "(2, 100)")
    findings = detect_shortcuts([("test_old.py", old, None), ("test_new.py", None, new)])
    assert [f.kind for f in findings] == [ShortcutKind.EXPECTED_VALUE_CHANGED]
    assert "(74)" in findings[0].message
    assert "(100)" in findings[0].message
    assert "Lines: 2, 4." in findings[0].message


@pytest.mark.parametrize(
    "before, after, wider",
    [
        ("3, abs=1e-9", "3, rel=None, abs=1e-6", True),
        ("3, rel=None, abs=1e-9", "3, abs=1e-6", True),
        ("3, abs=1e-9", "3, rel=None, abs=None", True),
        ("3, rel=1e-9, abs=None", "3, rel=1e-6, abs=None", True),
        ("3, None, 1e-9", "3, None, 1e-6", True),
        ("3, None, None", "3, rel=1e-5, abs=None", True),
        ("0, rel=None, abs=None", "0, rel=None, abs=1e-6", True),
        ("[3, -3], abs=1e-9", "[3, -3], rel=None, abs=1e-6", True),
        ("3, abs=1e-6", "3, rel=None, abs=1e-9", False),
        ("3", "3, rel=None, abs=None", False),
        ("3, abs=None", "3", False),
        ("3, rel=None, abs=1e-6", "3, abs=1e-6", False),
    ],
)
def test_none_approx_options_use_pytest_defaults(before: str, after: str, wider: bool) -> None:
    old = f"import pytest\ndef test_result():\n    assert run() == pytest.approx({before})\n"
    new = old.replace(f"approx({before})", f"approx({after})")
    assert [f.kind for f in detect_shortcuts([("test_app.py", old, new)])] == (
        [ShortcutKind.ASSERTION_LOOSENED] if wider else []
    )


@pytest.mark.parametrize("destination_exists", [False, True])
@pytest.mark.parametrize("keep_origin", [False, True])
def test_helper_move_does_not_report_a_preexisting_literal_branch(
    destination_exists: bool, keep_origin: bool
) -> None:
    helper = "def run(x):\n    if x == 7:\n        return 73\n    return x\n"
    other = "def other(x):\n    return x + 1\n"
    test = "def test_result():\n    assert run(7) == 73\n"
    changes = [
        ("source.py", helper + other, helper + other if keep_origin else other),
        ("destination.py", other if destination_exists else None, other + helper),
        ("test_app.py", test, test),
    ]
    assert detect_shortcuts(changes) == []


def test_moved_helper_still_reports_a_new_literal_branch() -> None:
    helper = "def run(x):\n    if x == 7:\n        return 73\n    return x\n"
    moved = helper.replace("    return x\n", "    if x == 8:\n        return 99\n    return x\n")
    other = "def other(x):\n    return x + 1\n"
    test = "def test_result():\n    assert run(7) == 73\n    assert run(8) == 99\n"
    findings = detect_shortcuts(
        [
            ("source.py", helper + other, other),
            ("destination.py", other, other + moved),
            ("test_app.py", test, test),
        ]
    )
    assert [(f.kind, f.path, f.line) for f in findings] == [
        (ShortcutKind.LITERAL_BRANCH, "destination.py", 6)
    ]
    assert "branches on 8" in findings[0].message
    assert "branches on 7" not in findings[0].message
