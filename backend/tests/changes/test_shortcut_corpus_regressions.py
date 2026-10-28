"""Small original examples of patterns discovered in the read-only history scan."""

import pytest

from oversight.changes import ShortcutKind, detect_shortcuts


@pytest.mark.parametrize("check", ["value == other", "run() == 7", "state is None", "a != b"])
def test_reversing_comparison_is_clean(check: str) -> None:
    left, op, right = check.split()
    old = f"def test_case():\n    assert {check}\n"
    new = f"def test_case():\n    assert {right} {op} {left}\n"
    assert detect_shortcuts([("test_case.py", old, new)]) == []


def test_expected_value_on_left_is_reviewed() -> None:
    old = "def test_case():\n    assert [7, 8] == run()\n"
    new = old.replace("[7, 8]", "[7, 9]")
    findings = detect_shortcuts([("test_case.py", old, new)])
    assert [f.kind for f in findings] == [ShortcutKind.EXPECTED_VALUE_CHANGED]
    assert "check the new expected value" in findings[0].message


def test_local_rename_with_unchanged_setup_is_clean() -> None:
    old = "def test_case():\n    result = run()\n    assert result == 7\n"
    new = old.replace("result", "answer")
    assert detect_shortcuts([("test_case.py", old, new)]) == []


def test_local_rename_does_not_hide_assertion_removal() -> None:
    old = "def test_case():\n    result = run()\n    assert result == 7\n"
    new = "def test_case():\n    answer = run()\n    assert answer\n"
    assert [f.kind for f in detect_shortcuts([("test_case.py", old, new)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]


@pytest.mark.parametrize(
    "before, after",
    [
        ("assertEquals", "assertEqual"),
        ("assertNotEquals", "assertNotEqual"),
        ("assert_", "assertTrue"),
    ],
)
def test_unittest_alias_modernization_is_clean(before: str, after: str) -> None:
    args = "run()" if before == "assert_" else "run(), 7"
    old = f"class TestCase:\n    def test_case(self):\n        self.{before}({args})\n"
    new = old.replace(before, after)
    assert detect_shortcuts([("test_case.py", old, new)]) == []


def test_alias_still_detects_weakening() -> None:
    old = "class TestCase:\n    def test_case(self):\n        self.assertEquals(run(), 7)\n"
    new = old.replace("assertEquals(run(), 7)", "assertTrue(run())")
    assert [f.kind for f in detect_shortcuts([("test_case.py", old, new)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]


def test_rename_with_docstring_edit_is_clean() -> None:
    old = 'def test_old():\n    """Old explanation."""\n    assert run() == 7\n'
    new = old.replace("test_old", "test_new").replace("Old explanation", "New explanation")
    assert detect_shortcuts([("test_case.py", old, new)]) == []


def test_docstring_edit_does_not_hide_replacement() -> None:
    old = 'def test_old():\n    """Old."""\n    assert run() == 7\n'
    new = old.replace("test_old", "test_new").replace("Old", "New").replace("run()", "other()")
    assert [f.kind for f in detect_shortcuts([("test_case.py", old, new)])] == [
        ShortcutKind.TEST_REMOVED
    ]


@pytest.mark.parametrize(
    "before, after",
    [
        ('"item={x!r}".format(x=value)', 'f"item={value!r}"'),
        ('"{0}: {1}".format(label, value)', 'f"{label}: {value}"'),
        ('set(["a", "b"])', '{"a", "b"}'),
    ],
)
def test_syntax_modernization_preserves_check(before: str, after: str) -> None:
    old = f"def test_case():\n    assert result == {before}\n"
    new = old.replace(before, after)
    assert detect_shortcuts([("test_case.py", old, new)]) == []


def test_changed_fstring_value_still_needs_review() -> None:
    old = 'def test_case():\n    assert result == "item={x}".format(x=value)\n'
    new = 'def test_case():\n    assert result == f"item={other}"\n'
    assert [f.kind for f in detect_shortcuts([("test_case.py", old, new)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]


def test_shadowed_set_is_not_normalized() -> None:
    old = 'def test_case(set):\n    assert result == set(["a"])\n'
    new = 'def test_case(set):\n    assert result == {"a"}\n'
    assert detect_shortcuts([("test_case.py", old, new)])


@pytest.mark.parametrize(
    "before, after",
    [
        ("self.assertEqual(run(), 7)", "assert run() == 7"),
        ("self.assertTrue(ready)", "assert ready"),
        ("self.assertFalse(ready)", "assert not ready"),
        ("self.assertIsNone(result)", "assert result is None"),
        ("self.assertIsNotNone(result)", "assert result is not None"),
    ],
)
def test_unittest_to_pytest_migration_is_clean(before: str, after: str) -> None:
    old = f"class TestCase:\n    def test_case(self):\n        {before}\n"
    new = old.replace(before, after)
    assert detect_shortcuts([("test_case.py", old, new)]) == []


def test_parametrize_argument_rename_preserves_check() -> None:
    old = (
        'import pytest\n@pytest.mark.parametrize("item", [7, 8])\n'
        "def test_case(item):\n    assert run(item) == 3\n"
    )
    new = old.replace("item", "value")
    assert detect_shortcuts([("test_case.py", old, new)]) == []


def test_fixture_swap_is_not_assumed_to_be_parameter_rename() -> None:
    old = "def test_case(item):\n    assert run(item) == 3\n"
    new = old.replace("item", "value")
    assert [f.kind for f in detect_shortcuts([("test_case.py", old, new)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]


def test_renaming_test_preserves_existing_helper_placeholder() -> None:
    old = "def test_old():\n    def callback():\n        pass\n    assert run(callback) == 7\n"
    new = old.replace("test_old", "test_new")
    assert detect_shortcuts([("test_case.py", old, new)]) == []


def test_moving_test_with_import_edit_preserves_placeholder() -> None:
    old = (
        "from old.module import run\ndef test_case():\n"
        "    def callback():\n        pass\n    assert run(callback) == 7\n"
    )
    new = old.replace("old.module", "new.module")
    assert (
        detect_shortcuts([("tests/test_old.py", old, None), ("tests/test_new.py", None, new)]) == []
    )


@pytest.mark.parametrize(
    "before, after",
    [
        ("not result", "result is None"),
        ("not result", "result is False"),
        ("result", "result is True"),
        ("result == None", "result is None"),
    ],
)
def test_identity_tightening_is_clean(before: str, after: str) -> None:
    old = f"def test_case():\n    assert {before}\n"
    new = old.replace(f"assert {before}", f"assert {after}")
    assert detect_shortcuts([("test_case.py", old, new)]) == []


def test_tightening_does_not_hide_unrelated_removed_check() -> None:
    old = "def test_case():\n    assert not result\n    assert saved\n"
    new = "def test_case():\n    assert result is None\n"
    assert [f.kind for f in detect_shortcuts([("test_case.py", old, new)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]


def test_identical_parameter_tables_are_not_cross_matched() -> None:
    old = (
        'import pytest\n@pytest.mark.parametrize("left", [True, False])\n'
        '@pytest.mark.parametrize("right", [True, False])\n'
        "def test_case(left, right):\n    assert run(left, right) == 7\n"
    )
    new = old + "    assert ready\n"
    assert detect_shortcuts([("test_case.py", old, new)]) == []


@pytest.mark.parametrize("method", ["assertRaises", "assertRaisesRegex"])
def test_callable_raises_migrates_to_context_manager(method: str) -> None:
    regex = ', "invalid"' if method.endswith("Regex") else ""
    match = ', match="invalid"' if regex else ""
    old = (
        "class TestCase:\n    def test_case(self):\n"
        f"        self.{method}(ValueError{regex}, run, 7)\n"
    )
    new = (
        "import pytest\nclass TestCase:\n    def test_case(self):\n"
        f"        with pytest.raises(ValueError{match}):\n            run(7)\n"
    )
    assert detect_shortcuts([("test_case.py", old, new)]) == []
    assert detect_shortcuts([("test_case.py", new, old)]) == []


def test_removed_callable_pytest_raises_is_reported() -> None:
    old = "import pytest\ndef test_case():\n    pytest.raises(ValueError, run, 7)\n"
    new = "import pytest\ndef test_case():\n    run(7)\n"
    assert [f.kind for f in detect_shortcuts([("test_case.py", old, new)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]


def test_unicode_prefix_only_edit_is_clean() -> None:
    old = 'def test_case():\n    assert run() == u"value"\n'
    new = old.replace('u"value"', '"value"')
    assert detect_shortcuts([("test_case.py", old, new)]) == []


def test_unicode_prefix_does_not_introduce_literal_branch() -> None:
    old = 'def run(x):\n    if x == u"case":\n        return 7\n    return 8\n'
    new = old.replace('u"case"', '"case"')
    test = 'def test_case():\n    assert run("case") == 7\n'
    assert detect_shortcuts([("module.py", old, new), ("test_case.py", test, test)]) == []
