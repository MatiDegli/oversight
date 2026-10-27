"""Precision regressions written for the labeled Oversight findings."""

import pytest

from oversight.changes import ShortcutKind, detect_shortcuts


@pytest.mark.parametrize("value", ["0", "1", "-1", "''", "None", "True", "False"])
def test_ordinary_sentinel_guard_is_clean(value: str) -> None:
    test = f"def test_result():\n    assert run({value}) == 73\n"
    old = "def run(value):\n    return value\n"
    new = f"def run(value):\n    if value == {value}:\n        return 73\n    return value\n"
    assert detect_shortcuts([("app.py", old, new), ("test_app.py", test, test)]) == []


@pytest.mark.parametrize("statement", ["return 73", "result = 73"])
def test_matching_expected_result_is_hardcoding(statement: str) -> None:
    test = "def test_result():\n    assert run('special') == 73\n"
    old = "def run(value):\n    return value\n"
    new = f"def run(value):\n    if value == 'special':\n        {statement}\n    return value\n"
    findings = detect_shortcuts([("app.py", old, new), ("test_app.py", test, test)])
    assert [f.kind for f in findings] == [ShortcutKind.LITERAL_BRANCH]


def test_guard_with_unrelated_result_is_clean() -> None:
    test = "def test_result():\n    assert run('special') == 73\n"
    old = "def run(value):\n    return value\n"
    new = "def run(value):\n    if value == 'special':\n        return 99\n    return value\n"
    assert detect_shortcuts([("app.py", old, new), ("test_app.py", test, test)]) == []


def test_expected_value_from_another_test_does_not_match() -> None:
    test = (
        "def test_first():\n    assert run('special') == 73\n"
        "def test_second():\n    assert run('other') == 99\n"
    )
    old = "def run(value):\n    return value\n"
    new = "def run(value):\n    if value == 'special':\n        return 99\n    return value\n"
    assert detect_shortcuts([("app.py", old, new), ("test_app.py", test, test)]) == []


@pytest.mark.parametrize(
    "source",
    [
        "def run():\n    try:\n        remove()\n    except FileNotFoundError:\n        pass\n",
        "def run():\n    with lock():\n        pass\n",
        "async def test_run():\n    async with session():\n        pass\n",
        "class Quiet(Server):\n    def log_message(self, *args):\n        pass\n",
        "class Visitor:\n    def visit_Name(self, node):\n        pass\n",
        "def run():\n    # TODO is discussed here\n    return 73\n",
        "def run():\n    # Documentation: TODO markers describe future work\n    return 73\n",
    ],
)
def test_intentional_noop_or_comment_is_clean(source: str) -> None:
    assert (
        detect_shortcuts([("test_app.py" if "test_run" in source else "app.py", None, source)])
        == []
    )


@pytest.mark.parametrize("statement", ["pass", "...", "raise NotImplementedError", "TODO()"])
def test_unimplemented_non_test_function_is_reported(statement: str) -> None:
    old = "def run():\n    return 73\n"
    new = f"def run():\n    {statement}\n"
    assert [f.kind for f in detect_shortcuts([("app.py", old, new)])] == [ShortcutKind.STUB_LEFT]


def test_conditional_not_implemented_is_reported() -> None:
    old = "def run(value):\n    return value\n"
    new = (
        "def run(value):\n    if value == 73:\n        raise NotImplementedError\n"
        "    return value\n"
    )
    assert [f.kind for f in detect_shortcuts([("app.py", old, new)])] == [ShortcutKind.STUB_LEFT]


def test_actionable_todo_comment_is_still_reported() -> None:
    old = "def run():\n    return 73\n"
    new = "def run():\n    # TODO: implement missing validation\n    return 73\n"
    assert [f.kind for f in detect_shortcuts([("app.py", old, new)])] == [ShortcutKind.STUB_LEFT]
