"""Checks retain their identity through moves, grouping and stronger exceptions."""

import pytest

from oversight.changes import ShortcutKind, detect_shortcuts


@pytest.mark.parametrize("move", [False, True])
def test_renamed_strengthened_test_is_not_removed(move: bool) -> None:
    old = "def test_status():\n    assert status() == 73\n"
    new = "def test_full_status():\n    assert status() == 73\n    assert metadata() == 'ready'\n"
    changes = [("test_old.py", old, new)]
    if move:
        changes = [("test_old.py", old, None), ("test_new.py", None, new)]
    assert detect_shortcuts(changes) == []


def test_renaming_with_fewer_checks_stays_removed() -> None:
    old = "def test_status():\n    assert status() == 73\n    assert metadata() == 'ready'\n"
    new = "def test_replacement():\n    assert status() == 73\n"
    assert [f.kind for f in detect_shortcuts([("test_app.py", old, new)])] == [
        ShortcutKind.TEST_REMOVED
    ]


def test_renamed_test_with_changed_expectation_is_reviewed() -> None:
    old = "def test_status():\n    assert status() == 73\n"
    new = "def test_replacement():\n    assert status() == 99\n"
    assert detect_shortcuts([("test_app.py", old, new)])


def test_renaming_does_not_hide_loosening() -> None:
    old = "def test_status():\n    assert status() == 73\n"
    new = "def test_replacement():\n    assert status()\n"
    assert detect_shortcuts([("test_app.py", old, new)])


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("rename", [False, True])
def test_tuple_grouping_keeps_checks(reverse: bool, rename: bool) -> None:
    split = "def test_status():\n    assert result.code == 73\n    assert result.error is None\n"
    grouped = "def test_status():\n    assert (result.code, result.error) == (73, None)\n"
    # Equality to None does not preserve identity; use equality on both sides.
    split = split.replace(" is None", " == None")
    old, new = (grouped, split) if reverse else (split, grouped)
    if rename:
        new = new.replace("test_status", "test_grouped_status")
    assert detect_shortcuts([("test_app.py", old, new)]) == []


def test_tuple_grouping_still_reports_a_lost_check() -> None:
    old = "def test_status():\n    assert result.code == 73\n    assert result.error == 'bad'\n"
    new = "def test_status():\n    assert (result.code,) == (73,)\n"
    assert [f.kind for f in detect_shortcuts([("test_app.py", old, new)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]


def test_adding_exception_match_strengthens_the_check() -> None:
    old = "import pytest\ndef test_status():\n    with pytest.raises(ValueError):\n        run()\n"
    new = old.replace("pytest.raises(ValueError)", "pytest.raises(ValueError, match='bad')")
    assert detect_shortcuts([("test_app.py", old, new)]) == []
    assert [f.kind for f in detect_shortcuts([("test_app.py", new, old)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]


def test_setup_lines_never_count_as_checks() -> None:
    old = "def test_status():\n    root.mkdir()\n    assert status() == 73\n"
    new = old.replace("root.mkdir()", "(root / 'bin').mkdir(parents=True)")
    assert detect_shortcuts([("test_app.py", old, new)]) == []


def test_local_rename_with_added_setup_keeps_checks() -> None:
    old = "def test_status():\n    result = run()\n    assert result.code == 73\n"
    new = (
        "def test_status():\n    extra = prepare()\n    output = run()\n"
        "    assert output.code == 73\n"
    )
    assert detect_shortcuts([("test_app.py", old, new)]) == []


def test_exact_count_message_loosened_remains_a_finding() -> None:
    old = "def test_status():\n    assert not result.ok and 'appears 3 times' in result.content\n"
    new = old.replace("appears 3 times", "appears more than once")
    assert [f.kind for f in detect_shortcuts([("test_app.py", old, new)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]


def test_findings_are_grouped_by_kind_and_path_with_all_lines() -> None:
    old = "def test_status():\n    assert first()\n    assert second()\n"
    new = "def test_status():\n    run()\n"
    findings = detect_shortcuts([("test_app.py", old, new)])
    assert len(findings) == 1
    assert findings[0].line == 2
    assert "Lines: 2, 3." in findings[0].message
    other = detect_shortcuts([("test_app.py", old, new), ("test_other.py", old, new)])
    assert len(other) == 2


def test_rewording_retains_explicit_numeric_constraints() -> None:
    old = (
        "def test_status():\n"
        "    assert 'low did not reduce reasoning' in result.text\n"
        "    assert '(5 tokens against 5' in result.text\n"
    )
    new = (
        "def test_status():\n"
        "    assert 'low gave 5 reasoning tokens and the default 5' in result.text\n"
    )
    assert detect_shortcuts([("test_app.py", old, new)]) == []


def test_moving_check_inside_a_block_is_clean() -> None:
    old = "def test_status():\n    assert status() == 73\n    with lock():\n        run()\n"
    new = "def test_status():\n    with lock():\n        run()\n        assert status() == 73\n"
    assert detect_shortcuts([("test_app.py", old, new)]) == []


@pytest.mark.parametrize("phrase", ["at least 3 times", "more than 3 times", "roughly 3 times"])
def test_rewording_does_not_hide_weaker_numeric_language(phrase: str) -> None:
    old = "def test_status():\n    assert 'appears 3 times' in result.content\n"
    new = old.replace("appears 3 times", "appears " + phrase)
    assert [f.kind for f in detect_shortcuts([("test_app.py", old, new)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]
