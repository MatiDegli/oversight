"""Regressions for digit-only wording overlap and equal-count binding capture."""

import pytest

from oversight.changes import ShortcutKind, detect_shortcuts


@pytest.mark.parametrize("number", ["4", "4.5"])
@pytest.mark.parametrize("removed_first", [False, True])
def test_numbers_do_not_count_as_shared_words(number: str, removed_first: bool) -> None:
    checks = [
        "    assert 'access limit 3' in result.text\n",
        f"    assert 'access denied {number}' in result.text\n",
    ]
    if removed_first:
        checks.reverse()
    old = "def test_status():\n" + "".join(checks)
    new = f"def test_status():\n    assert 'access limit: 3 retries {number}' in result.text\n"
    findings = detect_shortcuts([("test_app.py", old, new)])
    assert [(f.kind, f.path, f.line) for f in findings] == [
        (ShortcutKind.ASSERTION_LOOSENED, "test_app.py", 2)
    ]
    assert "Lines: 2, 3." in findings[0].message


@pytest.mark.parametrize(
    "replacement",
    [
        "second: int = run()\n",
        "(second := run())\n",
        "for second in run():\n        use(second)\n",
        "for other, second in run():\n        use(other, second)\n",
        "async for second in run():\n        use(second)\n",
        "with run() as second:\n        use(second)\n",
        "with run() as (other, second):\n        use(other, second)\n",
        "async with run() as second:\n        use(second)\n",
    ],
)
@pytest.mark.parametrize("plain_before", [False, True])
def test_equal_store_count_does_not_hide_a_changed_binding(
    replacement: str, plain_before: bool
) -> None:
    setup = "second = cached()\n" if plain_before else replacement.replace("run()", "cached()")
    old = f"async def test_status():\n    {setup}    assert second == 73\n"
    new = old.replace(setup, replacement)
    findings = detect_shortcuts([("test_app.py", old, new)])
    assert [(f.kind, f.path, f.line) for f in findings] == [
        (ShortcutKind.ASSERTION_LOOSENED, "test_app.py", len(old.splitlines()))
    ]


@pytest.mark.parametrize(
    "setup",
    [
        "second: int = cached()\n",
        "(second := cached())\n",
        "for second in cached():\n        use(second)\n",
        "with cached() as second:\n        use(second)\n",
    ],
)
def test_unchanged_binding_with_extra_setup_keeps_its_check(setup: str) -> None:
    old = f"def test_status():\n    {setup}    assert second == 73\n"
    new = old.replace("    assert", "    prepare()\n    assert")
    assert detect_shortcuts([("test_app.py", old, new)]) == []


def test_an_annotation_alone_does_not_change_the_checked_value() -> None:
    old = "def test_status():\n    second = cached()\n    assert second == 73\n"
    new = old.replace("second =", "second: int =", 1)
    assert detect_shortcuts([("test_app.py", old, new)]) == []


def test_safe_rename_keeps_a_dependent_binding_unchanged() -> None:
    old = (
        "def test_status():\n    first = run()\n    second: int = use(first)\n"
        "    assert second == 73\n"
    )
    new = old.replace("first", "renamed")
    assert detect_shortcuts([("test_app.py", old, new)]) == []
