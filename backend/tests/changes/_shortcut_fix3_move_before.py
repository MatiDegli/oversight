"""Inlined historical source fixtures; tests never invoke git."""

OLD = (
    '"""Before/after fixtures for the shortcut detector (one per detection)."'
    '""\n\nfrom oversight.review import Finding, ShortcutKind, detect_shortcuts'
    "\n\n\ndef _kinds(findings: list[Finding]) -> list[ShortcutKind]:\n    return"
    " [f.kind for f in findings]\n\n\n# -- test removed ------------------------"
    "-----------------------------------\n\n\ndef test_test_function_deleted() -"
    '> None:\n    old = (\n        "def test_add():\\n    assert add(1, 2) == 3\\'
    'n\\ndef test_sub():\\n    assert sub(2, 1) == 1\\n"\n    )\n    new = "def te'
    'st_add():\\n    assert add(1, 2) == 3\\n"\n    findings = detect_shortcuts('
    '[("tests/test_calc.py", old, new)])\n    assert _kinds(findings) == [Shor'
    'tcutKind.TEST_REMOVED]\n    assert findings[0].path == "tests/test_calc.p'
    'y"\n    assert "test_sub" in findings[0].message\n\n\ndef test_test_renamed_'
    'so_pytest_no_longer_collects_it() -> None:\n    old = "def test_add():\\n '
    '   assert add(1, 2) == 3\\n"\n    new = "def check_add():\\n    assert add('
    '1, 2) == 3\\n"\n    findings = detect_shortcuts([("test_calc.py", old, new'
    ")])\n    assert _kinds(findings) == [ShortcutKind.TEST_REMOVED]\n\n\ndef tes"
    't_test_renamed_to_another_test_name_is_clean() -> None:\n    old = "def t'
    'est_add():\\n    assert add(1, 2) == 3\\n"\n    new = "def test_addition():'
    '\\n    assert add(1, 2) == 3\\n"\n    assert detect_shortcuts([("test_calc.'
    'py", old, new)]) == []\n\n\ndef test_test_file_deleted() -> None:\n    old ='
    ' "def test_add():\\n    assert add(1, 2) == 3\\n"\n    findings = detect_sh'
    'ortcuts([("tests/test_calc.py", old, None)])\n    assert _kinds(findings)'
    " == [ShortcutKind.TEST_REMOVED]\n    assert findings[0].line == 1\n\n\ndef t"
    'est_test_file_conventions() -> None:\n    old = "def test_add():\\n    ass'
    'ert add(1, 2) == 3\\n"\n    for path in ("test_calc.py", "calc_test.py", "'
    'tests/test_calc.py", "tests/conftest.py"):\n        findings = detect_sho'
    "rtcuts([(path, old, None)])\n        assert _kinds(findings) == [Shortcut"
    "Kind.TEST_REMOVED], path\n\n\n# -- assertion removed or loosened ----------"
    "--------------------------------\n\n\ndef test_assert_eq_becoming_bare_asse"
    'rt_is_loosened() -> None:\n    old = "def test_add():\\n    result = add(1'
    ', 2)\\n    assert result == 3\\n"\n    new = "def test_add():\\n    result ='
    ' add(1, 2)\\n    assert result\\n"\n    findings = detect_shortcuts([("test'
    '_calc.py", old, new)])\n    assert _kinds(findings) == [ShortcutKind.ASSE'
    "RTION_LOOSENED]\n\n\ndef test_assert_equal_becoming_assert_true_is_loosened"
    '() -> None:\n    old = (\n        "import unittest\\n\\n"\n        "class Tes'
    'tCalc(unittest.TestCase):\\n"\n        "    def test_add(self):\\n"\n       '
    ' "        self.assertEqual(add(1, 2), 3)\\n"\n    )\n    new = (\n        "i'
    'mport unittest\\n\\n"\n        "class TestCalc(unittest.TestCase):\\n"\n     '
    '   "    def test_add(self):\\n"\n        "        self.assertTrue(add(1, 2'
    '))\\n"\n    )\n    findings = detect_shortcuts([("test_calc.py", old, new)]'
    ")\n    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]\n\n\ndef"
    ' test_eq_becoming_in_is_loosened() -> None:\n    old = "def test_add():\\n'
    '    assert add(1, 2) == 3\\n"\n    new = "def test_add():\\n    assert add('
    '1, 2) in [3, 4]\\n"\n    findings = detect_shortcuts([("test_calc.py", old'
    ", new)])\n    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED"
    ']\n\n\ndef test_eq_becoming_gte_is_loosened() -> None:\n    old = "def test_'
    'add():\\n    assert add(1, 2) == 3\\n"\n    new = "def test_add():\\n    ass'
    'ert add(1, 2) >= 3\\n"\n    findings = detect_shortcuts([("test_calc.py", '
    "old, new)])\n    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSE"
    "NED]\n\n\ndef test_wider_approx_tolerance_is_loosened() -> None:\n    old = "
    '"import pytest\\n\\ndef test_price():\\n    assert price() == pytest.approx'
    '(3.0, rel=1e-6)\\n"\n    new = "import pytest\\n\\ndef test_price():\\n    as'
    'sert price() == pytest.approx(3.0, rel=1e-3)\\n"\n    findings = detect_sh'
    'ortcuts([("test_calc.py", old, new)])\n    assert _kinds(findings) == [Sh'
    "ortcutKind.ASSERTION_LOOSENED]\n\n\ndef test_assertion_removed_entirely() -"
    '> None:\n    old = "def test_add():\\n    assert add(1, 2) == 3\\n    asser'
    't add(0, 0) == 0\\n"\n    new = "def test_add():\\n    assert add(1, 2) == '
    '3\\n"\n    findings = detect_shortcuts([("test_calc.py", old, new)])\n    a'
    "ssert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]\n\n\n# -- skip "
    "or xfail added ------------------------------------------------------\n\n\n"
    'def test_pytest_mark_skip_decorator_added() -> None:\n    old = "def test'
    '_add():\\n    assert add(1, 2) == 3\\n"\n    new = (\n        "import pytest'
    "\\n\\n@pytest.mark.skip(reason='slow')\\n\"\n        \"def test_add():\\n    as"
    'sert add(1, 2) == 3\\n"\n    )\n    findings = detect_shortcuts([("test_cal'
    'c.py", old, new)])\n    assert _kinds(findings) == [ShortcutKind.SKIP_ADD'
    'ED]\n\n\ndef test_pytest_skip_call_added() -> None:\n    old = "def test_add'
    '():\\n    assert add(1, 2) == 3\\n"\n    new = "import pytest\\n\\ndef test_a'
    "dd():\\n    pytest.skip('slow')\\n    assert add(1, 2) == 3\\n\"\n    finding"
    's = detect_shortcuts([("test_calc.py", old, new)])\n    assert _kinds(fin'
    "dings) == [ShortcutKind.SKIP_ADDED]\n\n\ndef test_unittest_skip_decorator_a"
    'dded() -> None:\n    old = (\n        "import unittest\\n\\n"\n        "class'
    ' TestCalc(unittest.TestCase):\\n"\n        "    def test_add(self):\\n"\n   '
    '     "        self.assertEqual(add(1, 2), 3)\\n"\n    )\n    new = (\n      '
    '  "import unittest\\n\\n"\n        "class TestCalc(unittest.TestCase):\\n"\n '
    '       "    @unittest.skip(\'slow\')\\n"\n        "    def test_add(self):\\n'
    '"\n        "        self.assertEqual(add(1, 2), 3)\\n"\n    )\n    findings '
    '= detect_shortcuts([("test_calc.py", old, new)])\n    assert _kinds(findi'
    "ngs) == [ShortcutKind.SKIP_ADDED]\n\n\ndef test_xfail_decorator_added() -> "
    'None:\n    old = "def test_add():\\n    assert add(1, 2) == 3\\n"\n    new ='
    ' "import pytest\\n\\n@pytest.mark.xfail\\ndef test_add():\\n    assert add(1'
    ', 2) == 3\\n"\n    findings = detect_shortcuts([("test_calc.py", old, new)'
    "])\n    assert _kinds(findings) == [ShortcutKind.SKIP_ADDED]\n\n\n# -- expec"
    "ted value changed ---------------------------------------------------\n\n\n"
    'def test_expected_value_changed() -> None:\n    old = "def test_add():\\n '
    '   assert add(1, 2) == 3\\n"\n    new = "def test_add():\\n    assert add(1'
    ', 2) == 4\\n"\n    findings = detect_shortcuts([("test_calc.py", old, new)'
    "])\n    assert _kinds(findings) == [ShortcutKind.EXPECTED_VALUE_CHANGED]\n"
    '    assert "check the new expected value" in findings[0].message\n\n\ndef t'
    "est_expected_value_changed_in_assert_equal() -> None:\n    old = (\n      "
    '  "import unittest\\n\\n"\n        "class TestCalc(unittest.TestCase):\\n"\n '
    '       "    def test_add(self):\\n"\n        "        self.assertEqual(add'
    '(1, 2), 3)\\n"\n    )\n    new = (\n        "import unittest\\n\\n"\n        "c'
    'lass TestCalc(unittest.TestCase):\\n"\n        "    def test_add(self):\\n"'
    '\n        "        self.assertEqual(add(1, 2), 4)\\n"\n    )\n    findings ='
    ' detect_shortcuts([("test_calc.py", old, new)])\n    assert _kinds(findin'
    'gs) == [ShortcutKind.EXPECTED_VALUE_CHANGED]\n    assert "check the new e'
    'xpected value" in findings[0].message\n\n\n# -- literal branch in non-test '
    "code ------------------------------------------\n\n\ndef test_literal_branc"
    'h_in_non_test_code() -> None:\n    test_old = "def test_discount():\\n    '
    "assert discount('magic-code') == 0\\n\"\n    test_new = test_old\n    prod_o"
    'ld = "def discount(code):\\n    return 10\\n"\n    prod_new = (\n        "de'
    "f discount(code):\\n    if code == 'magic-code':\\n        return 0\\n    r"
    'eturn 10\\n"\n    )\n    findings = detect_shortcuts(\n        [("shop/prici'
    'ng.py", prod_old, prod_new), ("tests/test_pricing.py", test_old, test_ne'
    "w)]\n    )\n    assert _kinds(findings) == [ShortcutKind.LITERAL_BRANCH]\n "
    '   assert findings[0].path == "shop/pricing.py"\n\n\n# -- stubs in a touche'
    "d function ----------------------------------------------\n\n\ndef test_pas"
    's_left_in_touched_function() -> None:\n    old = "def add(a, b):\\n    ret'
    'urn a + b\\n"\n    new = "def add(a, b):\\n    pass\\n"\n    findings = detec'
    't_shortcuts([("calc.py", old, new)])\n    assert _kinds(findings) == [Sho'
    "rtcutKind.STUB_LEFT]\n\n\ndef test_ellipsis_left_in_touched_function() -> N"
    'one:\n    old = "def add(a, b):\\n    return a + b\\n"\n    new = "def add(a'
    ', b):\\n    ...\\n"\n    findings = detect_shortcuts([("calc.py", old, new)'
    "])\n    assert _kinds(findings) == [ShortcutKind.STUB_LEFT]\n\n\ndef test_no"
    't_implemented_left_in_touched_function() -> None:\n    old = "def add(a, '
    'b):\\n    return a + b\\n"\n    new = "def add(a, b):\\n    raise NotImpleme'
    'ntedError\\n"\n    findings = detect_shortcuts([("calc.py", old, new)])\n  '
    "  assert _kinds(findings) == [ShortcutKind.STUB_LEFT]\n\n\ndef test_todo_le"
    'ft_in_touched_function() -> None:\n    old = "def add(a, b):\\n    return '
    'a + b\\n"\n    new = "def add(a, b):\\n    # TODO: handle negatives\\n    re'
    'turn a + b\\n"\n    findings = detect_shortcuts([("calc.py", old, new)])\n '
    "   assert _kinds(findings) == [ShortcutKind.STUB_LEFT]\n\n\ndef test_fixme_"
    'left_in_touched_method() -> None:\n    old = "class Calc:\\n    def add(se'
    'lf, a, b):\\n        return a + b\\n"\n    new = "class Calc:\\n    def add('
    'self, a, b):\\n        # FIXME: overflow\\n        return a + b\\n"\n    fin'
    'dings = detect_shortcuts([("calc.py", old, new)])\n    assert _kinds(find'
    "ings) == [ShortcutKind.STUB_LEFT]\n\n\ndef test_stub_in_untouched_function_"
    'is_clean() -> None:\n    old = "def add(a, b):\\n    return a + b\\n\\ndef s'
    'ub(a, b):\\n    pass\\n"\n    new = "def add(a, b):\\n    return a + b + 0\\n'
    '\\ndef sub(a, b):\\n    pass\\n"\n    assert detect_shortcuts([("calc.py", o'
    "ld, new)]) == []\n\n\n# -- parse errors -----------------------------------"
    "----------------------------\n\n\ndef test_unparseable_file_yields_finding_"
    'not_crash() -> None:\n    findings = detect_shortcuts([("calc.py", "def a'
    'dd(a, b):\\n    return a + b\\n", "def add(:\\n")])\n    assert _kinds(findi'
    "ngs) == [ShortcutKind.PARSE_ERROR]\n\n\ndef test_unparseable_new_file_yield"
    's_finding() -> None:\n    findings = detect_shortcuts([("calc.py", None, '
    '"def add(:\\n")])\n    assert _kinds(findings) == [ShortcutKind.PARSE_ERRO'
    "R]\n\n\n# -- clean diffs --------------------------------------------------"
    "----------------\n\n\ndef test_clean_production_change() -> None:\n    old ="
    ' "def add(a, b):\\n    return a + b\\n"\n    new = "def add(a, b):\\n    tot'
    'al = a + b\\n    return total\\n"\n    assert detect_shortcuts([("calc.py",'
    " old, new)]) == []\n\n\ndef test_clean_test_change_adding_an_assertion() ->"
    ' None:\n    old = "def test_add():\\n    assert add(1, 2) == 3\\n"\n    new '
    '= "def test_add():\\n    assert add(1, 2) == 3\\n    assert add(0, 1) == 1'
    '\\n"\n    assert detect_shortcuts([("test_calc.py", old, new)]) == []\n\n\nde'
    'f test_clean_new_test_file() -> None:\n    new = "def test_add():\\n    as'
    'sert add(1, 2) == 3\\n"\n    assert detect_shortcuts([("test_calc.py", Non'
    "e, new)]) == []\n\n\ndef test_clean_tighter_approx_tolerance() -> None:\n   "
    ' old = "import pytest\\n\\ndef test_price():\\n    assert price() == pytest'
    '.approx(3.0, rel=1e-3)\\n"\n    new = "import pytest\\n\\ndef test_price():\\'
    'n    assert price() == pytest.approx(3.0, rel=1e-9)\\n"\n    assert detect'
    '_shortcuts([("test_calc.py", old, new)]) == []\n\n\ndef test_legitimate_exp'
    "ected_value_change_is_only_a_review_finding() -> None:\n    # A correct b"
    "ehaviour change updates the expectation: exactly one\n    # expected-valu"
    'e finding, and no loosened/removed finding beside it.\n    old = "def tes'
    't_add():\\n    assert add(1, 2) == 3\\n"\n    new = "def test_add():\\n    a'
    'ssert add(1, 2) == 30\\n"\n    findings = detect_shortcuts([("test_calc.py'
    '", old, new)])\n    assert _kinds(findings) == [ShortcutKind.EXPECTED_VAL'
    "UE_CHANGED]\n\n\ndef test_findings_are_sorted_by_path_line_kind() -> None:\n"
    '    findings = detect_shortcuts(\n        [\n            ("b.py", "def f()'
    ':\\n    return 1\\n", "def f():\\n    pass\\n"),\n            ("a.py", "def g'
    '():\\n    return 1\\n", "def g():\\n    ...\\n"),\n        ]\n    )\n    assert'
    " [(f.path, f.line, f.kind) for f in findings] == sorted(\n        (f.path"
    ", f.line, f.kind) for f in findings\n    )\n    assert [f.path for f in fi"
    'ndings] == ["a.py", "b.py"]\n'
)
