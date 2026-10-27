"""Regression fixtures for the second shortcut-detector review."""

import pytest

from oversight.changes import ShortcutKind, detect_shortcuts


@pytest.mark.parametrize(
    "replacement, expected",
    [
        ("assert sub(5, 2) == 3", ShortcutKind.TEST_REMOVED),
        ("assert add(1, 2)", ShortcutKind.ASSERTION_LOOSENED),
        ("assert add(1, 2) == 4", ShortcutKind.TEST_REMOVED),
    ],
)
def test_replacement_is_not_a_rename(replacement: str, expected: ShortcutKind) -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    new = f"def test_other():\n    {replacement}\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert [f.kind for f in findings] == [expected]


def test_assert_message_change_is_clean() -> None:
    old = 'def test_f():\n    assert f() == 3, "old"\n'
    new = old.replace('"old"', '"new"')
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


def test_preserved_raises_with_new_input_is_clean() -> None:
    old = "import pytest\ndef test_f():\n    with pytest.raises(ValueError):\n        f(1)\n"
    new = old.replace("f(1)", "f(2)")
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


@pytest.mark.parametrize(
    "prefix, signature, setup",
    [
        ("from pytest import skip\n", "skip", ""),
        ("from pytest import skip\n", "", "    skip = cache\n"),
        ("from pytest import skip\nskip = cache\n", "", ""),
        ("def helper():\n    from pytest import skip\n", "", ""),
    ],
)
def test_shadowed_or_other_scope_skip_is_clean(prefix: str, signature: str, setup: str) -> None:
    old = f"{prefix}def test_f({signature}):\n{setup}    assert f() == 3\n"
    new = old.replace("    assert", '    skip("cache")\n    assert')
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


def test_single_parametrize_collection_expected_change() -> None:
    old = (
        "import pytest\n@pytest.mark.parametrize('expected', [[1, 2]])\n"
        "def test_f(expected):\n    assert f() == expected\n"
    )
    new = old.replace("[1, 2]", "[1, 3]")
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert [f.kind for f in findings] == [ShortcutKind.EXPECTED_VALUE_CHANGED]
    assert "check the new expected value" in findings[0].message


def test_single_parametrize_collection_expected_is_not_input() -> None:
    test = (
        "import pytest\n@pytest.mark.parametrize('expected', [[1, 2]])\n"
        "def test_f(expected):\n    assert f() == expected\n"
    )
    old = "def f(x):\n    return x\n"
    new = "def f(x):\n    if x == 2:\n        return 1\n    return x\n"
    assert detect_shortcuts([("calc.py", old, new), ("test_calc.py", test, test)]) == []


def test_reordered_duplicate_parametrize_inputs_are_clean() -> None:
    old = (
        "import pytest\n@pytest.mark.parametrize('x,expected', [(1, 2), (1, 3)])\n"
        "def test_f(x, expected):\n    assert f(x) == expected\n"
    )
    new = old.replace("[(1, 2), (1, 3)]", "[(1, 3), (1, 2)]")
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


def test_nested_collected_class_method_removed() -> None:
    old = (
        "class TestOuter:\n    class TestInner:\n"
        "        def test_f(self):\n            assert f() == 3\n"
    )
    new = "class TestOuter:\n    class TestInner:\n        description = 'empty'\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert [f.kind for f in findings] == [ShortcutKind.TEST_REMOVED]
    assert "TestOuter.TestInner.test_f" in findings[0].message


def test_moved_skipped_file_is_clean() -> None:
    source = "import pytest\n@pytest.mark.skip\ndef test_f():\n    assert f() == 3\n"
    assert (
        detect_shortcuts([("tests/test_old.py", source, None), ("tests/test_new.py", None, source)])
        == []
    )


def test_reversed_comparison_retains_call_inputs() -> None:
    test = "def test_f():\n    assert 5 == f(7)\n"
    old = "def f(x):\n    return x + 1\n"
    new = "def f(x):\n    if x == 7:\n        return 5\n    return x + 1\n"
    findings = detect_shortcuts([("calc.py", old, new), ("test_calc.py", test, test)])
    assert [f.kind for f in findings] == [ShortcutKind.LITERAL_BRANCH]


def test_parametrize_name_uses_the_shared_resolver() -> None:
    # The removed fallback and independent traversals must not turn an unrelated
    # method into a pytest decorator just because its final name matches.
    old = "@cases.parametrize('expected', [1])\ndef test_f(expected):\n    assert f() == expected\n"
    new = old.replace("[1]", "[2]")
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


def test_rename_with_only_local_names_changed_is_clean() -> None:
    old = "def test_f(value):\n    result = f(value)\n    assert result == 3\n"
    new = "def test_result(number):\n    actual = f(number)\n    assert actual == 3\n"
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


def test_rename_with_changed_import_target_requests_review() -> None:
    old = "from calc import add as op\ndef test_add():\n    assert op(1, 2) == 3\n"
    new = "from calc import sub as op\ndef test_sub():\n    assert op(1, 2) == 3\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert [f.kind for f in findings] == [ShortcutKind.TEST_REMOVED]


def test_duplicate_parametrize_inputs_still_report_changed_expected() -> None:
    old = (
        "import pytest\n@pytest.mark.parametrize('x,expected', [(1, 2), (1, 3), (1, 3)])\n"
        "def test_f(x, expected):\n    assert f(x) == expected\n"
    )
    new = old.replace("[(1, 2), (1, 3), (1, 3)]", "[(1, 3), (1, 4), (1, 2)]")
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert [f.kind for f in findings] == [ShortcutKind.EXPECTED_VALUE_CHANGED]
    assert "(4)" in findings[0].message


def test_raises_context_duplicates_are_consumed_once() -> None:
    old = (
        "import pytest\ndef test_f():\n"
        "    with pytest.raises(ValueError):\n        f(1)\n"
        "    with pytest.raises(ValueError):\n        f(2)\n"
    )
    new = old.replace("    with pytest.raises(ValueError):\n        f(2)\n", "    f(2)\n")
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert [f.kind for f in findings] == [ShortcutKind.ASSERTION_LOOSENED]


def test_moved_file_with_class_and_module_skips_is_clean() -> None:
    source = (
        "import pytest\npytestmark = pytest.mark.xfail\n"
        "@pytest.mark.skip\nclass TestCalc:\n    def test_f(self):\n        assert f() == 3\n"
    )
    assert (
        detect_shortcuts([("tests/test_old.py", source, None), ("tests/test_new.py", None, source)])
        == []
    )


def test_renamed_skip_preserves_baseline_but_new_skip_is_reported() -> None:
    old = "import pytest\n@pytest.mark.skip\ndef test_f():\n    assert f() == 3\n"
    new = old.replace("test_f", "test_result")
    assert detect_shortcuts([("test_calc.py", old, new)]) == []
    new = new.replace("@pytest.mark.skip", "@pytest.mark.skip\n@pytest.mark.xfail")
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert [f.kind for f in findings] == [ShortcutKind.SKIP_ADDED]


@pytest.mark.parametrize(
    "cases, names",
    [("[[1, 2]]", "'x'"), ("[([1, 2],)]", "['x']"), ("[pytest.param([1, 2])]", "'x'")],
)
def test_single_collection_parametrize_input_is_retained(cases: str, names: str) -> None:
    test = (
        f"import pytest\n@pytest.mark.parametrize({names}, {cases})\n"
        "def test_f(x):\n    assert f(x) == 3\n"
    )
    old = "def f(x):\n    return x\n"
    new = "def f(x):\n    if x == 2:\n        return 3\n    return x\n"
    findings = detect_shortcuts([("calc.py", old, new), ("test_calc.py", test, test)])
    assert [f.kind for f in findings] == [ShortcutKind.LITERAL_BRANCH]


def test_reversed_comparison_expected_is_not_an_input() -> None:
    test = "def test_f():\n    assert 5 == f(0)\n"
    old = "def f(x):\n    return x\n"
    new = "def f(x):\n    if x == 5:\n        return 3\n    return x\n"
    assert detect_shortcuts([("calc.py", old, new), ("test_calc.py", test, test)]) == []
