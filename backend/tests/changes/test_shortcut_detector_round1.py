"""Regression coverage retained from the first shortcut-detector review."""

from oversight.changes import Finding, ShortcutKind, detect_shortcuts


def _kinds(findings: list[Finding]) -> list[ShortcutKind]:
    return [finding.kind for finding in findings]


# -- round 1 fixes ------------------------------------------------------------


def test_moved_test_file_unchanged_is_clean() -> None:
    old = (
        "def test_add():\n    assert add(1, 2) == 3\n\ndef test_sub():\n    assert sub(2, 1) == 1\n"
    )
    findings = detect_shortcuts([("tests/test_a.py", old, None), ("tests/test_b.py", None, old)])
    assert findings == []


def test_rename_with_extracted_variable_is_clean() -> None:
    old = "def test_x():\n    assert add(1, 2) == 3\n"
    new = "def test_result():\n    result = add(1, 2)\n    assert result == 3\n"
    # The new precision rule pairs this equivalent test by its body and callee.
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert findings == []


def test_duplicate_bodies_do_not_hide_a_removal() -> None:
    old = "def test_a():\n    assert add(1, 2) == 3\n\ndef test_b():\n    assert add(1, 2) == 3\n"
    new = "def test_c():\n    assert add(1, 2) == 3\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.TEST_REMOVED]


def test_swapping_assertions_is_clean() -> None:
    old = "def test_f():\n    assert f() == 2\n    assert f() == 3\n"
    new = "def test_f():\n    assert f() == 3\n    assert f() == 2\n"
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


def test_replacing_an_assertion_is_still_reported() -> None:
    old = "def test_f():\n    assert add(1, 2) == 3\n    assert sub(5, 3) == 2\n"
    new = "def test_f():\n    assert add(1, 2) == 3\n    assert mul(2, 3) == 6\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]


def test_skip_cache_decorator_is_clean() -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    new = "@skip_cache\ndef test_add():\n    assert add(1, 2) == 3\n"
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


def test_skip_resolved_through_imports() -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    new = "from unittest import skip\n\n@skip('slow')\ndef test_add():\n    assert add(1, 2) == 3\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.SKIP_ADDED]


def test_literal_branch_ignores_parametrize_expected_column() -> None:
    test_src = (
        "import pytest\n\n"
        "@pytest.mark.parametrize('x,expected', [('input', 'expected-value')])\n"
        "def test_f(x, expected):\n    assert f(x) == expected\n"
    )
    prod_old = "def f(v):\n    return v\n"
    prod_new = "def f(v):\n    if v == 'expected-value':\n        return 'x'\n    return v\n"
    findings = detect_shortcuts(
        [("pricing.py", prod_old, prod_new), ("tests/test_pricing.py", test_src, test_src)]
    )
    assert findings == []


def test_literal_branch_ignores_assert_expected_side() -> None:
    test_src = "def test_f():\n    assert f('input') == 'magic-expected'\n"
    prod_old = "def f(v):\n    return v\n"
    prod_new = "def f(v):\n    if v == 'magic-expected':\n        return 'x'\n    return v\n"
    findings = detect_shortcuts(
        [("pricing.py", prod_old, prod_new), ("tests/test_pricing.py", test_src, test_src)]
    )
    assert findings == []


def test_todo_in_untouched_inner_function_is_clean() -> None:
    old = "def f():\n    def g():\n        # TODO: later\n        return 1\n    return g()\n"
    new = "def f():\n    def g():\n        # TODO: later\n        return 1\n    return g() + 1\n"
    assert detect_shortcuts([("calc.py", old, new)]) == []


def test_removing_pytest_raises_is_loosened() -> None:
    old = "import pytest\n\ndef test_f():\n    with pytest.raises(ValueError):\n        f()\n"
    new = "import pytest\n\ndef test_f():\n    f()\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]


def test_removing_assert_raises_is_loosened() -> None:
    old = (
        "import unittest\n\n"
        "class TestCalc(unittest.TestCase):\n"
        "    def test_f(self):\n"
        "        with self.assertRaises(ValueError):\n"
        "            f()\n"
    )
    new = (
        "import unittest\n\n"
        "class TestCalc(unittest.TestCase):\n"
        "    def test_f(self):\n"
        "        f()\n"
    )
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]


def test_approx_positional_abs_tolerance_widened() -> None:
    old = (
        "import pytest\n\n"
        "def test_price():\n    assert price() == pytest.approx(3.0, 1e-6, 1e-12)\n"
    )
    new = (
        "import pytest\n\ndef test_price():\n    assert price() == pytest.approx(3.0, 1e-6, 1e-2)\n"
    )
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]


def test_approx_positional_rel_tolerance_widened() -> None:
    old = "import pytest\n\ndef test_price():\n    assert price() == pytest.approx(3.0, 1e-6)\n"
    new = "import pytest\n\ndef test_price():\n    assert price() == pytest.approx(3.0, 1e-2)\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]


def test_skip_added_to_class() -> None:
    old = "class TestCalc:\n    def test_add(self):\n        assert add(1, 2) == 3\n"
    new = (
        "import pytest\n\n"
        "@pytest.mark.skip(reason='slow')\n"
        "class TestCalc:\n    def test_add(self):\n        assert add(1, 2) == 3\n"
    )
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.SKIP_ADDED]


def test_module_level_pytest_skip_is_skip_added() -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    new = (
        "import pytest\n\n"
        "pytest.skip('slow', allow_module_level=True)\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n"
    )
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.SKIP_ADDED]


def test_module_level_pytestmark_skip_is_skip_added() -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    new = (
        "import pytest\n\n"
        "pytestmark = pytest.mark.skip(reason='slow')\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n"
    )
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.SKIP_ADDED]


def test_expected_value_changed_collections_and_negative() -> None:
    cases = [
        ("assert f() == [2, 3]\n", "assert f() == [2, 4]\n"),
        ("assert f() == (2, 3)\n", "assert f() == (2, 4)\n"),
        ("assert f() == {'a': 2}\n", "assert f() == {'a': 3}\n"),
        ("assert f() == -3\n", "assert f() == -4\n"),
    ]
    for old_assert, new_assert in cases:
        old = f"def test_f():\n    {old_assert}"
        new = f"def test_f():\n    {new_assert}"
        findings = detect_shortcuts([("test_calc.py", old, new)])
        assert _kinds(findings) == [ShortcutKind.EXPECTED_VALUE_CHANGED], (old, new)


def test_expected_value_changed_in_parametrize() -> None:
    old = (
        "import pytest\n\n"
        "@pytest.mark.parametrize('x,expected', [(1, 2)])\n"
        "def test_f(x, expected):\n    assert f(x) == expected\n"
    )
    new = (
        "import pytest\n\n"
        "@pytest.mark.parametrize('x,expected', [(1, 3)])\n"
        "def test_f(x, expected):\n    assert f(x) == expected\n"
    )
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.EXPECTED_VALUE_CHANGED]


def test_renaming_test_class_so_pytest_no_longer_collects_it() -> None:
    old = "class TestCalc:\n    def test_add(self):\n        assert add(1, 2) == 3\n"
    new = "class Helper:\n    def test_add(self):\n        assert add(1, 2) == 3\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.TEST_REMOVED]


def test_class_with_init_is_not_collected() -> None:
    old = (
        "class TestCalc:\n"
        "    def __init__(self):\n        pass\n"
        "    def test_add(self):\n        assert add(1, 2) == 3\n"
    )
    new = "def helper():\n    return 1\n"
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


def test_nested_test_function_is_not_a_test() -> None:
    old = (
        "def test_outer():\n"
        "    def test_inner():\n"
        "        assert add(1, 2) == 3\n"
        "    test_inner()\n"
        "    assert add(2, 2) == 4\n"
    )
    new = "def test_outer():\n    assert add(2, 2) == 4\n"
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


def test_literal_branch_on_zero_input() -> None:
    test_src = "def test_f():\n    assert f(0) == 5\n"
    prod_old = "def f(x):\n    return x + 1\n"
    prod_new = "def f(x):\n    if x == 0:\n        return 99\n    return x + 1\n"
    findings = detect_shortcuts(
        [("calc.py", prod_old, prod_new), ("tests/test_calc.py", test_src, test_src)]
    )
    assert findings == []


def test_literal_branch_still_excludes_bool_and_none() -> None:
    test_src = "def test_f():\n    assert f(True) == 5\n"
    prod_old = "def f(x):\n    return x\n"
    prod_new = "def f(x):\n    if x == True:\n        return 99\n    return x\n"
    findings = detect_shortcuts(
        [("calc.py", prod_old, prod_new), ("tests/test_calc.py", test_src, test_src)]
    )
    assert findings == []


def test_fixture_return_literal_is_a_known_limit() -> None:
    test_src = (
        "import pytest\n\n"
        "@pytest.fixture\n"
        "def code():\n    return 'magic'\n\n"
        "def test_f(code):\n    assert f(code) == 1\n"
    )
    prod_old = "def f(v):\n    return v\n"
    prod_new = "def f(v):\n    if v == 'magic':\n        return 'x'\n    return v\n"
    findings = detect_shortcuts(
        [("calc.py", prod_old, prod_new), ("tests/test_calc.py", test_src, test_src)]
    )
    assert findings == []


def test_findings_are_sorted_by_path_line_kind() -> None:
    findings = detect_shortcuts(
        [
            ("b.py", "def f():\n    return 1\n", "def f():\n    pass\n"),
            ("a.py", "def g():\n    return 1\n", "def g():\n    ...\n"),
        ]
    )
    assert [(f.path, f.line, f.kind) for f in findings] == sorted(
        (f.path, f.line, f.kind) for f in findings
    )
    assert [f.path for f in findings] == ["a.py", "b.py"]
