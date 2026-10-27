"""Before/after fixtures for the shortcut detector (one per detection)."""

from oversight.review import Finding, ShortcutKind, detect_shortcuts


def _kinds(findings: list[Finding]) -> list[ShortcutKind]:
    return [f.kind for f in findings]


# -- test removed -----------------------------------------------------------


def test_test_function_deleted() -> None:
    old = (
        "def test_add():\n    assert add(1, 2) == 3\n\ndef test_sub():\n    assert sub(2, 1) == 1\n"
    )
    new = "def test_add():\n    assert add(1, 2) == 3\n"
    findings = detect_shortcuts([("tests/test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.TEST_REMOVED]
    assert findings[0].path == "tests/test_calc.py"
    assert "test_sub" in findings[0].message


def test_test_renamed_so_pytest_no_longer_collects_it() -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    new = "def check_add():\n    assert add(1, 2) == 3\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.TEST_REMOVED]


def test_test_renamed_to_another_test_name_is_clean() -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    new = "def test_addition():\n    assert add(1, 2) == 3\n"
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


def test_test_file_deleted() -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    findings = detect_shortcuts([("tests/test_calc.py", old, None)])
    assert _kinds(findings) == [ShortcutKind.TEST_REMOVED]
    assert findings[0].line == 1


def test_test_file_conventions() -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    for path in ("test_calc.py", "calc_test.py", "tests/test_calc.py", "tests/conftest.py"):
        findings = detect_shortcuts([(path, old, None)])
        assert _kinds(findings) == [ShortcutKind.TEST_REMOVED], path


# -- assertion removed or loosened ------------------------------------------


def test_assert_eq_becoming_bare_assert_is_loosened() -> None:
    old = "def test_add():\n    result = add(1, 2)\n    assert result == 3\n"
    new = "def test_add():\n    result = add(1, 2)\n    assert result\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]


def test_assert_equal_becoming_assert_true_is_loosened() -> None:
    old = (
        "import unittest\n\n"
        "class TestCalc(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(1, 2), 3)\n"
    )
    new = (
        "import unittest\n\n"
        "class TestCalc(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertTrue(add(1, 2))\n"
    )
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]


def test_eq_becoming_in_is_loosened() -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    new = "def test_add():\n    assert add(1, 2) in [3, 4]\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]


def test_eq_becoming_gte_is_loosened() -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    new = "def test_add():\n    assert add(1, 2) >= 3\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]


def test_wider_approx_tolerance_is_loosened() -> None:
    old = "import pytest\n\ndef test_price():\n    assert price() == pytest.approx(3.0, rel=1e-6)\n"
    new = "import pytest\n\ndef test_price():\n    assert price() == pytest.approx(3.0, rel=1e-3)\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]


def test_assertion_removed_entirely() -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n    assert add(0, 0) == 0\n"
    new = "def test_add():\n    assert add(1, 2) == 3\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]


# -- skip or xfail added ------------------------------------------------------


def test_pytest_mark_skip_decorator_added() -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    new = (
        "import pytest\n\n@pytest.mark.skip(reason='slow')\n"
        "def test_add():\n    assert add(1, 2) == 3\n"
    )
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.SKIP_ADDED]


def test_pytest_skip_call_added() -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    new = "import pytest\n\ndef test_add():\n    pytest.skip('slow')\n    assert add(1, 2) == 3\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.SKIP_ADDED]


def test_unittest_skip_decorator_added() -> None:
    old = (
        "import unittest\n\n"
        "class TestCalc(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(1, 2), 3)\n"
    )
    new = (
        "import unittest\n\n"
        "class TestCalc(unittest.TestCase):\n"
        "    @unittest.skip('slow')\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(1, 2), 3)\n"
    )
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.SKIP_ADDED]


def test_xfail_decorator_added() -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    new = "import pytest\n\n@pytest.mark.xfail\ndef test_add():\n    assert add(1, 2) == 3\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.SKIP_ADDED]


# -- expected value changed ---------------------------------------------------


def test_expected_value_changed() -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    new = "def test_add():\n    assert add(1, 2) == 4\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.EXPECTED_VALUE_CHANGED]
    assert "check the new expected value" in findings[0].message


def test_expected_value_changed_in_assert_equal() -> None:
    old = (
        "import unittest\n\n"
        "class TestCalc(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(1, 2), 3)\n"
    )
    new = (
        "import unittest\n\n"
        "class TestCalc(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(1, 2), 4)\n"
    )
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.EXPECTED_VALUE_CHANGED]
    assert "check the new expected value" in findings[0].message


# -- literal branch in non-test code ------------------------------------------


def test_literal_branch_in_non_test_code() -> None:
    test_old = "def test_discount():\n    assert discount('magic-code') == 0\n"
    test_new = test_old
    prod_old = "def discount(code):\n    return 10\n"
    prod_new = (
        "def discount(code):\n    if code == 'magic-code':\n        return 0\n    return 10\n"
    )
    findings = detect_shortcuts(
        [("shop/pricing.py", prod_old, prod_new), ("tests/test_pricing.py", test_old, test_new)]
    )
    assert _kinds(findings) == [ShortcutKind.LITERAL_BRANCH]
    assert findings[0].path == "shop/pricing.py"


# -- stubs in a touched function ----------------------------------------------


def test_pass_left_in_touched_function() -> None:
    old = "def add(a, b):\n    return a + b\n"
    new = "def add(a, b):\n    pass\n"
    findings = detect_shortcuts([("calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.STUB_LEFT]


def test_ellipsis_left_in_touched_function() -> None:
    old = "def add(a, b):\n    return a + b\n"
    new = "def add(a, b):\n    ...\n"
    findings = detect_shortcuts([("calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.STUB_LEFT]


def test_not_implemented_left_in_touched_function() -> None:
    old = "def add(a, b):\n    return a + b\n"
    new = "def add(a, b):\n    raise NotImplementedError\n"
    findings = detect_shortcuts([("calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.STUB_LEFT]


def test_todo_left_in_touched_function() -> None:
    old = "def add(a, b):\n    return a + b\n"
    new = "def add(a, b):\n    # TODO: handle negatives\n    return a + b\n"
    findings = detect_shortcuts([("calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.STUB_LEFT]


def test_fixme_left_in_touched_method() -> None:
    old = "class Calc:\n    def add(self, a, b):\n        return a + b\n"
    new = "class Calc:\n    def add(self, a, b):\n        # FIXME: overflow\n        return a + b\n"
    findings = detect_shortcuts([("calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.STUB_LEFT]


def test_stub_in_untouched_function_is_clean() -> None:
    old = "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    pass\n"
    new = "def add(a, b):\n    return a + b + 0\n\ndef sub(a, b):\n    pass\n"
    assert detect_shortcuts([("calc.py", old, new)]) == []


# -- parse errors ---------------------------------------------------------------


def test_unparseable_file_yields_finding_not_crash() -> None:
    findings = detect_shortcuts([("calc.py", "def add(a, b):\n    return a + b\n", "def add(:\n")])
    assert _kinds(findings) == [ShortcutKind.PARSE_ERROR]


def test_unparseable_new_file_yields_finding() -> None:
    findings = detect_shortcuts([("calc.py", None, "def add(:\n")])
    assert _kinds(findings) == [ShortcutKind.PARSE_ERROR]


# -- clean diffs ------------------------------------------------------------------


def test_clean_production_change() -> None:
    old = "def add(a, b):\n    return a + b\n"
    new = "def add(a, b):\n    total = a + b\n    return total\n"
    assert detect_shortcuts([("calc.py", old, new)]) == []


def test_clean_test_change_adding_an_assertion() -> None:
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    new = "def test_add():\n    assert add(1, 2) == 3\n    assert add(0, 1) == 1\n"
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


def test_clean_new_test_file() -> None:
    new = "def test_add():\n    assert add(1, 2) == 3\n"
    assert detect_shortcuts([("test_calc.py", None, new)]) == []


def test_clean_tighter_approx_tolerance() -> None:
    old = "import pytest\n\ndef test_price():\n    assert price() == pytest.approx(3.0, rel=1e-3)\n"
    new = "import pytest\n\ndef test_price():\n    assert price() == pytest.approx(3.0, rel=1e-9)\n"
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


def test_legitimate_expected_value_change_is_only_a_review_finding() -> None:
    # A correct behaviour change updates the expectation: exactly one
    # expected-value finding, and no loosened/removed finding beside it.
    old = "def test_add():\n    assert add(1, 2) == 3\n"
    new = "def test_add():\n    assert add(1, 2) == 30\n"
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert _kinds(findings) == [ShortcutKind.EXPECTED_VALUE_CHANGED]


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
