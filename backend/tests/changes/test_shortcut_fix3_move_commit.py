"""Inlined historical source fixtures; tests never invoke git."""

from oversight.changes import detect_shortcuts

from ._shortcut_fix3_move_before import OLD

NEW = (
    '"""Before/after fixtures for the shortcut detector (one per detection)."'
    '""\n\nfrom oversight.changes import Finding, ShortcutKind, detect_shortcut'
    "s\n\n\ndef _kinds(findings: list[Finding]) -> list[ShortcutKind]:\n    retur"
    "n [f.kind for f in findings]\n\n\n# -- test removed -----------------------"
    "------------------------------------\n\n\ndef test_test_function_deleted() "
    '-> None:\n    old = (\n        "def test_add():\\n    assert add(1, 2) == 3'
    '\\n\\ndef test_sub():\\n    assert sub(2, 1) == 1\\n"\n    )\n    new = "def t'
    'est_add():\\n    assert add(1, 2) == 3\\n"\n    findings = detect_shortcuts'
    '([("tests/test_calc.py", old, new)])\n    assert _kinds(findings) == [Sho'
    'rtcutKind.TEST_REMOVED]\n    assert findings[0].path == "tests/test_calc.'
    'py"\n    assert "test_sub" in findings[0].message\n\n\ndef test_test_renamed'
    '_so_pytest_no_longer_collects_it() -> None:\n    old = "def test_add():\\n'
    '    assert add(1, 2) == 3\\n"\n    new = "def check_add():\\n    assert add'
    '(1, 2) == 3\\n"\n    findings = detect_shortcuts([("test_calc.py", old, ne'
    "w)])\n    assert _kinds(findings) == [ShortcutKind.TEST_REMOVED]\n\n\ndef te"
    'st_test_renamed_to_another_test_name_is_clean() -> None:\n    old = "def '
    'test_add():\\n    assert add(1, 2) == 3\\n"\n    new = "def test_addition()'
    ':\\n    assert add(1, 2) == 3\\n"\n    assert detect_shortcuts([("test_calc'
    '.py", old, new)]) == []\n\n\ndef test_test_file_deleted() -> None:\n    old '
    '= "def test_add():\\n    assert add(1, 2) == 3\\n"\n    findings = detect_s'
    'hortcuts([("tests/test_calc.py", old, None)])\n    assert _kinds(findings'
    ") == [ShortcutKind.TEST_REMOVED]\n    assert findings[0].line == 1\n\n\ndef "
    'test_test_file_conventions() -> None:\n    old = "def test_add():\\n    as'
    'sert add(1, 2) == 3\\n"\n    for path in ("test_calc.py", "calc_test.py", '
    '"tests/test_calc.py", "tests/conftest.py"):\n        findings = detect_sh'
    "ortcuts([(path, old, None)])\n        assert _kinds(findings) == [Shortcu"
    "tKind.TEST_REMOVED], path\n\n\n# -- assertion removed or loosened ---------"
    "---------------------------------\n\n\ndef test_assert_eq_becoming_bare_ass"
    'ert_is_loosened() -> None:\n    old = "def test_add():\\n    result = add('
    '1, 2)\\n    assert result == 3\\n"\n    new = "def test_add():\\n    result '
    '= add(1, 2)\\n    assert result\\n"\n    findings = detect_shortcuts([("tes'
    't_calc.py", old, new)])\n    assert _kinds(findings) == [ShortcutKind.ASS'
    "ERTION_LOOSENED]\n\n\ndef test_assert_equal_becoming_assert_true_is_loosene"
    'd() -> None:\n    old = (\n        "import unittest\\n\\n"\n        "class Te'
    'stCalc(unittest.TestCase):\\n"\n        "    def test_add(self):\\n"\n      '
    '  "        self.assertEqual(add(1, 2), 3)\\n"\n    )\n    new = (\n        "'
    'import unittest\\n\\n"\n        "class TestCalc(unittest.TestCase):\\n"\n    '
    '    "    def test_add(self):\\n"\n        "        self.assertTrue(add(1, '
    '2))\\n"\n    )\n    findings = detect_shortcuts([("test_calc.py", old, new)'
    "])\n    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]\n\n\nde"
    'f test_eq_becoming_in_is_loosened() -> None:\n    old = "def test_add():\\'
    'n    assert add(1, 2) == 3\\n"\n    new = "def test_add():\\n    assert add'
    '(1, 2) in [3, 4]\\n"\n    findings = detect_shortcuts([("test_calc.py", ol'
    "d, new)])\n    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENE"
    'D]\n\n\ndef test_eq_becoming_gte_is_loosened() -> None:\n    old = "def test'
    '_add():\\n    assert add(1, 2) == 3\\n"\n    new = "def test_add():\\n    as'
    'sert add(1, 2) >= 3\\n"\n    findings = detect_shortcuts([("test_calc.py",'
    " old, new)])\n    assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOS"
    "ENED]\n\n\ndef test_wider_approx_tolerance_is_loosened() -> None:\n    old ="
    ' "import pytest\\n\\ndef test_price():\\n    assert price() == pytest.appro'
    'x(3.0, rel=1e-6)\\n"\n    new = "import pytest\\n\\ndef test_price():\\n    a'
    'ssert price() == pytest.approx(3.0, rel=1e-3)\\n"\n    findings = detect_s'
    'hortcuts([("test_calc.py", old, new)])\n    assert _kinds(findings) == [S'
    "hortcutKind.ASSERTION_LOOSENED]\n\n\ndef test_assertion_removed_entirely() "
    '-> None:\n    old = "def test_add():\\n    assert add(1, 2) == 3\\n    asse'
    'rt add(0, 0) == 0\\n"\n    new = "def test_add():\\n    assert add(1, 2) =='
    ' 3\\n"\n    findings = detect_shortcuts([("test_calc.py", old, new)])\n    '
    "assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]\n\n\n# -- skip"
    " or xfail added ------------------------------------------------------\n\n"
    '\ndef test_pytest_mark_skip_decorator_added() -> None:\n    old = "def tes'
    't_add():\\n    assert add(1, 2) == 3\\n"\n    new = (\n        "import pytes'
    "t\\n\\n@pytest.mark.skip(reason='slow')\\n\"\n        \"def test_add():\\n    a"
    'ssert add(1, 2) == 3\\n"\n    )\n    findings = detect_shortcuts([("test_ca'
    'lc.py", old, new)])\n    assert _kinds(findings) == [ShortcutKind.SKIP_AD'
    'DED]\n\n\ndef test_pytest_skip_call_added() -> None:\n    old = "def test_ad'
    'd():\\n    assert add(1, 2) == 3\\n"\n    new = "import pytest\\n\\ndef test_'
    "add():\\n    pytest.skip('slow')\\n    assert add(1, 2) == 3\\n\"\n    findin"
    'gs = detect_shortcuts([("test_calc.py", old, new)])\n    assert _kinds(fi'
    "ndings) == [ShortcutKind.SKIP_ADDED]\n\n\ndef test_unittest_skip_decorator_"
    'added() -> None:\n    old = (\n        "import unittest\\n\\n"\n        "clas'
    's TestCalc(unittest.TestCase):\\n"\n        "    def test_add(self):\\n"\n  '
    '      "        self.assertEqual(add(1, 2), 3)\\n"\n    )\n    new = (\n     '
    '   "import unittest\\n\\n"\n        "class TestCalc(unittest.TestCase):\\n"\n'
    '        "    @unittest.skip(\'slow\')\\n"\n        "    def test_add(self):\\'
    'n"\n        "        self.assertEqual(add(1, 2), 3)\\n"\n    )\n    findings'
    ' = detect_shortcuts([("test_calc.py", old, new)])\n    assert _kinds(find'
    "ings) == [ShortcutKind.SKIP_ADDED]\n\n\ndef test_xfail_decorator_added() ->"
    ' None:\n    old = "def test_add():\\n    assert add(1, 2) == 3\\n"\n    new '
    '= "import pytest\\n\\n@pytest.mark.xfail\\ndef test_add():\\n    assert add('
    '1, 2) == 3\\n"\n    findings = detect_shortcuts([("test_calc.py", old, new'
    ")])\n    assert _kinds(findings) == [ShortcutKind.SKIP_ADDED]\n\n\n# -- expe"
    "cted value changed ---------------------------------------------------\n\n"
    '\ndef test_expected_value_changed() -> None:\n    old = "def test_add():\\n'
    '    assert add(1, 2) == 3\\n"\n    new = "def test_add():\\n    assert add('
    '1, 2) == 4\\n"\n    findings = detect_shortcuts([("test_calc.py", old, new'
    ")])\n    assert _kinds(findings) == [ShortcutKind.EXPECTED_VALUE_CHANGED]"
    '\n    assert "check the new expected value" in findings[0].message\n\n\ndef '
    "test_expected_value_changed_in_assert_equal() -> None:\n    old = (\n     "
    '   "import unittest\\n\\n"\n        "class TestCalc(unittest.TestCase):\\n"\n'
    '        "    def test_add(self):\\n"\n        "        self.assertEqual(ad'
    'd(1, 2), 3)\\n"\n    )\n    new = (\n        "import unittest\\n\\n"\n        "'
    'class TestCalc(unittest.TestCase):\\n"\n        "    def test_add(self):\\n'
    '"\n        "        self.assertEqual(add(1, 2), 4)\\n"\n    )\n    findings '
    '= detect_shortcuts([("test_calc.py", old, new)])\n    assert _kinds(findi'
    'ngs) == [ShortcutKind.EXPECTED_VALUE_CHANGED]\n    assert "check the new '
    'expected value" in findings[0].message\n\n\n# -- literal branch in non-test'
    " code ------------------------------------------\n\n\ndef test_literal_bran"
    'ch_in_non_test_code() -> None:\n    test_old = "def test_discount():\\n   '
    " assert discount('magic-code') == 0\\n\"\n    test_new = test_old\n    prod_"
    'old = "def discount(code):\\n    return 10\\n"\n    prod_new = (\n        "d'
    "ef discount(code):\\n    if code == 'magic-code':\\n        return 0\\n    "
    'return 10\\n"\n    )\n    findings = detect_shortcuts(\n        [("shop/pric'
    'ing.py", prod_old, prod_new), ("tests/test_pricing.py", test_old, test_n'
    "ew)]\n    )\n    assert _kinds(findings) == [ShortcutKind.LITERAL_BRANCH]\n"
    '    assert findings[0].path == "shop/pricing.py"\n\n\n# -- stubs in a touch'
    "ed function ----------------------------------------------\n\n\ndef test_pa"
    'ss_left_in_touched_function() -> None:\n    old = "def add(a, b):\\n    re'
    'turn a + b\\n"\n    new = "def add(a, b):\\n    pass\\n"\n    findings = dete'
    'ct_shortcuts([("calc.py", old, new)])\n    assert _kinds(findings) == [Sh'
    "ortcutKind.STUB_LEFT]\n\n\ndef test_ellipsis_left_in_touched_function() -> "
    'None:\n    old = "def add(a, b):\\n    return a + b\\n"\n    new = "def add('
    'a, b):\\n    ...\\n"\n    findings = detect_shortcuts([("calc.py", old, new'
    ")])\n    assert _kinds(findings) == [ShortcutKind.STUB_LEFT]\n\n\ndef test_n"
    'ot_implemented_left_in_touched_function() -> None:\n    old = "def add(a,'
    ' b):\\n    return a + b\\n"\n    new = "def add(a, b):\\n    raise NotImplem'
    'entedError\\n"\n    findings = detect_shortcuts([("calc.py", old, new)])\n '
    "   assert _kinds(findings) == [ShortcutKind.STUB_LEFT]\n\n\ndef test_todo_l"
    'eft_in_touched_function() -> None:\n    old = "def add(a, b):\\n    return'
    ' a + b\\n"\n    new = "def add(a, b):\\n    # TODO: handle negatives\\n    r'
    'eturn a + b\\n"\n    findings = detect_shortcuts([("calc.py", old, new)])\n'
    "    assert _kinds(findings) == [ShortcutKind.STUB_LEFT]\n\n\ndef test_fixme"
    '_left_in_touched_method() -> None:\n    old = "class Calc:\\n    def add(s'
    'elf, a, b):\\n        return a + b\\n"\n    new = "class Calc:\\n    def add'
    '(self, a, b):\\n        # FIXME: overflow\\n        return a + b\\n"\n    fi'
    'ndings = detect_shortcuts([("calc.py", old, new)])\n    assert _kinds(fin'
    "dings) == [ShortcutKind.STUB_LEFT]\n\n\ndef test_stub_in_untouched_function"
    '_is_clean() -> None:\n    old = "def add(a, b):\\n    return a + b\\n\\ndef '
    'sub(a, b):\\n    pass\\n"\n    new = "def add(a, b):\\n    return a + b + 0\\'
    'n\\ndef sub(a, b):\\n    pass\\n"\n    assert detect_shortcuts([("calc.py", '
    "old, new)]) == []\n\n\n# -- parse errors ----------------------------------"
    "-----------------------------\n\n\ndef test_unparseable_file_yields_finding"
    '_not_crash() -> None:\n    findings = detect_shortcuts([("calc.py", "def '
    'add(a, b):\\n    return a + b\\n", "def add(:\\n")])\n    assert _kinds(find'
    "ings) == [ShortcutKind.PARSE_ERROR]\n\n\ndef test_unparseable_new_file_yiel"
    'ds_finding() -> None:\n    findings = detect_shortcuts([("calc.py", None,'
    ' "def add(:\\n")])\n    assert _kinds(findings) == [ShortcutKind.PARSE_ERR'
    "OR]\n\n\n# -- clean diffs -------------------------------------------------"
    "-----------------\n\n\ndef test_clean_production_change() -> None:\n    old "
    '= "def add(a, b):\\n    return a + b\\n"\n    new = "def add(a, b):\\n    to'
    'tal = a + b\\n    return total\\n"\n    assert detect_shortcuts([("calc.py"'
    ", old, new)]) == []\n\n\ndef test_clean_test_change_adding_an_assertion() -"
    '> None:\n    old = "def test_add():\\n    assert add(1, 2) == 3\\n"\n    new'
    ' = "def test_add():\\n    assert add(1, 2) == 3\\n    assert add(0, 1) == '
    '1\\n"\n    assert detect_shortcuts([("test_calc.py", old, new)]) == []\n\n\nd'
    'ef test_clean_new_test_file() -> None:\n    new = "def test_add():\\n    a'
    'ssert add(1, 2) == 3\\n"\n    assert detect_shortcuts([("test_calc.py", No'
    "ne, new)]) == []\n\n\ndef test_clean_tighter_approx_tolerance() -> None:\n  "
    '  old = "import pytest\\n\\ndef test_price():\\n    assert price() == pytes'
    't.approx(3.0, rel=1e-3)\\n"\n    new = "import pytest\\n\\ndef test_price():'
    '\\n    assert price() == pytest.approx(3.0, rel=1e-9)\\n"\n    assert detec'
    't_shortcuts([("test_calc.py", old, new)]) == []\n\n\ndef test_legitimate_ex'
    "pected_value_change_is_only_a_review_finding() -> None:\n    # A correct "
    "behaviour change updates the expectation: exactly one\n    # expected-val"
    'ue finding, and no loosened/removed finding beside it.\n    old = "def te'
    'st_add():\\n    assert add(1, 2) == 3\\n"\n    new = "def test_add():\\n    '
    'assert add(1, 2) == 30\\n"\n    findings = detect_shortcuts([("test_calc.p'
    'y", old, new)])\n    assert _kinds(findings) == [ShortcutKind.EXPECTED_VA'
    "LUE_CHANGED]\n\n\n# -- round 1 fixes --------------------------------------"
    "----------------------\n\n\ndef test_moved_test_file_unchanged_is_clean() -"
    '> None:\n    old = (\n        "def test_add():\\n    assert add(1, 2) == 3\\'
    'n\\ndef test_sub():\\n    assert sub(2, 1) == 1\\n"\n    )\n    findings = de'
    'tect_shortcuts([("tests/test_a.py", old, None), ("tests/test_b.py", None'
    ", old)])\n    assert findings == []\n\n\ndef test_rename_with_extracted_vari"
    'able_is_clean() -> None:\n    old = "def test_x():\\n    assert add(1, 2) '
    '== 3\\n"\n    new = "def test_result():\\n    result = add(1, 2)\\n    asser'
    't result == 3\\n"\n    assert detect_shortcuts([("test_calc.py", old, new)'
    "]) == []\n\n\ndef test_duplicate_bodies_do_not_hide_a_removal() -> None:\n  "
    '  old = "def test_a():\\n    assert add(1, 2) == 3\\n\\ndef test_b():\\n    '
    'assert add(1, 2) == 3\\n"\n    new = "def test_c():\\n    assert add(1, 2) '
    '== 3\\n"\n    findings = detect_shortcuts([("test_calc.py", old, new)])\n  '
    "  assert _kinds(findings) == [ShortcutKind.TEST_REMOVED]\n\n\ndef test_swap"
    'ping_assertions_is_clean() -> None:\n    old = "def test_f():\\n    assert'
    ' f() == 2\\n    assert f() == 3\\n"\n    new = "def test_f():\\n    assert f'
    '() == 3\\n    assert f() == 2\\n"\n    assert detect_shortcuts([("test_calc'
    '.py", old, new)]) == []\n\n\ndef test_replacing_an_assertion_is_still_repor'
    'ted() -> None:\n    old = "def test_f():\\n    assert add(1, 2) == 3\\n    '
    'assert sub(5, 3) == 2\\n"\n    new = "def test_f():\\n    assert add(1, 2) '
    '== 3\\n    assert mul(2, 3) == 6\\n"\n    findings = detect_shortcuts([("te'
    'st_calc.py", old, new)])\n    assert _kinds(findings) == [ShortcutKind.AS'
    "SERTION_LOOSENED]\n\n\ndef test_skip_cache_decorator_is_clean() -> None:\n  "
    '  old = "def test_add():\\n    assert add(1, 2) == 3\\n"\n    new = "@skip_'
    'cache\\ndef test_add():\\n    assert add(1, 2) == 3\\n"\n    assert detect_s'
    'hortcuts([("test_calc.py", old, new)]) == []\n\n\ndef test_skip_resolved_th'
    'rough_imports() -> None:\n    old = "def test_add():\\n    assert add(1, 2'
    ") == 3\\n\"\n    new = \"from unittest import skip\\n\\n@skip('slow')\\ndef tes"
    't_add():\\n    assert add(1, 2) == 3\\n"\n    findings = detect_shortcuts(['
    '("test_calc.py", old, new)])\n    assert _kinds(findings) == [ShortcutKin'
    "d.SKIP_ADDED]\n\n\ndef test_literal_branch_ignores_parametrize_expected_col"
    'umn() -> None:\n    test_src = (\n        "import pytest\\n\\n"\n        "@py'
    "test.mark.parametrize('x,expected', [('input', 'expected-value')])\\n\"\n  "
    '      "def test_f(x, expected):\\n    assert f(x) == expected\\n"\n    )\n  '
    '  prod_old = "def f(v):\\n    return v\\n"\n    prod_new = "def f(v):\\n    '
    "if v == 'expected-value':\\n        return 'x'\\n    return v\\n\"\n    findi"
    'ngs = detect_shortcuts(\n        [("pricing.py", prod_old, prod_new), ("t'
    'ests/test_pricing.py", test_src, test_src)]\n    )\n    assert findings =='
    " []\n\n\ndef test_literal_branch_ignores_assert_expected_side() -> None:\n  "
    "  test_src = \"def test_f():\\n    assert f('input') == 'magic-expected'\\n"
    '"\n    prod_old = "def f(v):\\n    return v\\n"\n    prod_new = "def f(v):\\n'
    "    if v == 'magic-expected':\\n        return 'x'\\n    return v\\n\"\n    f"
    'indings = detect_shortcuts(\n        [("pricing.py", prod_old, prod_new),'
    ' ("tests/test_pricing.py", test_src, test_src)]\n    )\n    assert finding'
    "s == []\n\n\ndef test_todo_in_untouched_inner_function_is_clean() -> None:\n"
    '    old = "def f():\\n    def g():\\n        # TODO: later\\n        return'
    ' 1\\n    return g()\\n"\n    new = "def f():\\n    def g():\\n        # TODO:'
    ' later\\n        return 1\\n    return g() + 1\\n"\n    assert detect_shortc'
    'uts([("calc.py", old, new)]) == []\n\n\ndef test_removing_pytest_raises_is_'
    'loosened() -> None:\n    old = "import pytest\\n\\ndef test_f():\\n    with '
    'pytest.raises(ValueError):\\n        f()\\n"\n    new = "import pytest\\n\\nd'
    'ef test_f():\\n    f()\\n"\n    findings = detect_shortcuts([("test_calc.py'
    '", old, new)])\n    assert _kinds(findings) == [ShortcutKind.ASSERTION_LO'
    "OSENED]\n\n\ndef test_removing_assert_raises_is_loosened() -> None:\n    old"
    ' = (\n        "import unittest\\n\\n"\n        "class TestCalc(unittest.Test'
    'Case):\\n"\n        "    def test_f(self):\\n"\n        "        with self.a'
    'ssertRaises(ValueError):\\n"\n        "            f()\\n"\n    )\n    new = '
    '(\n        "import unittest\\n\\n"\n        "class TestCalc(unittest.TestCas'
    'e):\\n"\n        "    def test_f(self):\\n"\n        "        f()\\n"\n    )\n '
    '   findings = detect_shortcuts([("test_calc.py", old, new)])\n    assert '
    "_kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]\n\n\ndef test_approx_"
    'positional_abs_tolerance_widened() -> None:\n    old = (\n        "import '
    'pytest\\n\\n"\n        "def test_price():\\n    assert price() == pytest.app'
    'rox(3.0, 1e-6, 1e-12)\\n"\n    )\n    new = (\n        "import pytest\\n\\ndef'
    ' test_price():\\n    assert price() == pytest.approx(3.0, 1e-6, 1e-2)\\n"\n'
    '    )\n    findings = detect_shortcuts([("test_calc.py", old, new)])\n    '
    "assert _kinds(findings) == [ShortcutKind.ASSERTION_LOOSENED]\n\n\ndef test_"
    'approx_positional_rel_tolerance_widened() -> None:\n    old = "import pyt'
    "est\\n\\ndef test_price():\\n    assert price() == pytest.approx(3.0, 1e-6)"
    '\\n"\n    new = "import pytest\\n\\ndef test_price():\\n    assert price() =='
    ' pytest.approx(3.0, 1e-2)\\n"\n    findings = detect_shortcuts([("test_cal'
    'c.py", old, new)])\n    assert _kinds(findings) == [ShortcutKind.ASSERTIO'
    'N_LOOSENED]\n\n\ndef test_skip_added_to_class() -> None:\n    old = "class T'
    'estCalc:\\n    def test_add(self):\\n        assert add(1, 2) == 3\\n"\n    '
    'new = (\n        "import pytest\\n\\n"\n        "@pytest.mark.skip(reason=\'s'
    'low\')\\n"\n        "class TestCalc:\\n    def test_add(self):\\n        asse'
    'rt add(1, 2) == 3\\n"\n    )\n    findings = detect_shortcuts([("test_calc.'
    'py", old, new)])\n    assert _kinds(findings) == [ShortcutKind.SKIP_ADDED'
    "]\n\n\ndef test_module_level_pytest_skip_is_skip_added() -> None:\n    old ="
    ' "def test_add():\\n    assert add(1, 2) == 3\\n"\n    new = (\n        "imp'
    "ort pytest\\n\\n\"\n        \"pytest.skip('slow', allow_module_level=True)\\n\\"
    'n"\n        "def test_add():\\n    assert add(1, 2) == 3\\n"\n    )\n    find'
    'ings = detect_shortcuts([("test_calc.py", old, new)])\n    assert _kinds('
    "findings) == [ShortcutKind.SKIP_ADDED]\n\n\ndef test_module_level_pytestmar"
    'k_skip_is_skip_added() -> None:\n    old = "def test_add():\\n    assert a'
    'dd(1, 2) == 3\\n"\n    new = (\n        "import pytest\\n\\n"\n        "pytest'
    "mark = pytest.mark.skip(reason='slow')\\n\\n\"\n        \"def test_add():\\n  "
    '  assert add(1, 2) == 3\\n"\n    )\n    findings = detect_shortcuts([("test'
    '_calc.py", old, new)])\n    assert _kinds(findings) == [ShortcutKind.SKIP'
    "_ADDED]\n\n\ndef test_expected_value_changed_collections_and_negative() -> "
    'None:\n    cases = [\n        ("assert f() == [2, 3]\\n", "assert f() == [2'
    ', 4]\\n"),\n        ("assert f() == (2, 3)\\n", "assert f() == (2, 4)\\n"),\n'
    "        (\"assert f() == {'a': 2}\\n\", \"assert f() == {'a': 3}\\n\"),\n      "
    '  ("assert f() == -3\\n", "assert f() == -4\\n"),\n    ]\n    for old_assert'
    ', new_assert in cases:\n        old = f"def test_f():\\n    {old_assert}"\n'
    '        new = f"def test_f():\\n    {new_assert}"\n        findings = dete'
    'ct_shortcuts([("test_calc.py", old, new)])\n        assert _kinds(finding'
    "s) == [ShortcutKind.EXPECTED_VALUE_CHANGED], (old, new)\n\n\ndef test_expec"
    'ted_value_changed_in_parametrize() -> None:\n    old = (\n        "import '
    'pytest\\n\\n"\n        "@pytest.mark.parametrize(\'x,expected\', [(1, 2)])\\n"'
    '\n        "def test_f(x, expected):\\n    assert f(x) == expected\\n"\n    )'
    '\n    new = (\n        "import pytest\\n\\n"\n        "@pytest.mark.parametri'
    "ze('x,expected', [(1, 3)])\\n\"\n        \"def test_f(x, expected):\\n    ass"
    'ert f(x) == expected\\n"\n    )\n    findings = detect_shortcuts([("test_ca'
    'lc.py", old, new)])\n    assert _kinds(findings) == [ShortcutKind.EXPECTE'
    "D_VALUE_CHANGED]\n\n\ndef test_renaming_test_class_so_pytest_no_longer_coll"
    'ects_it() -> None:\n    old = "class TestCalc:\\n    def test_add(self):\\n'
    '        assert add(1, 2) == 3\\n"\n    new = "class Helper:\\n    def test_'
    'add(self):\\n        assert add(1, 2) == 3\\n"\n    findings = detect_short'
    'cuts([("test_calc.py", old, new)])\n    assert _kinds(findings) == [Short'
    "cutKind.TEST_REMOVED]\n\n\ndef test_class_with_init_is_not_collected() -> N"
    'one:\n    old = (\n        "class TestCalc:\\n"\n        "    def __init__(s'
    'elf):\\n        pass\\n"\n        "    def test_add(self):\\n        assert '
    'add(1, 2) == 3\\n"\n    )\n    new = "def helper():\\n    return 1\\n"\n    as'
    'sert detect_shortcuts([("test_calc.py", old, new)]) == []\n\n\ndef test_nes'
    'ted_test_function_is_not_a_test() -> None:\n    old = (\n        "def test'
    '_outer():\\n"\n        "    def test_inner():\\n"\n        "        assert a'
    'dd(1, 2) == 3\\n"\n        "    test_inner()\\n"\n        "    assert add(2,'
    ' 2) == 4\\n"\n    )\n    new = "def test_outer():\\n    assert add(2, 2) == '
    '4\\n"\n    assert detect_shortcuts([("test_calc.py", old, new)]) == []\n\n\nd'
    'ef test_literal_branch_on_zero_input() -> None:\n    test_src = "def test'
    '_f():\\n    assert f(0) == 5\\n"\n    prod_old = "def f(x):\\n    return x +'
    ' 1\\n"\n    prod_new = "def f(x):\\n    if x == 0:\\n        return 99\\n    '
    'return x + 1\\n"\n    findings = detect_shortcuts(\n        [("calc.py", pr'
    'od_old, prod_new), ("tests/test_calc.py", test_src, test_src)]\n    )\n   '
    " assert _kinds(findings) == [ShortcutKind.LITERAL_BRANCH]\n\n\ndef test_lit"
    'eral_branch_still_excludes_bool_and_none() -> None:\n    test_src = "def '
    'test_f():\\n    assert f(True) == 5\\n"\n    prod_old = "def f(x):\\n    ret'
    'urn x\\n"\n    prod_new = "def f(x):\\n    if x == True:\\n        return 99'
    '\\n    return x\\n"\n    findings = detect_shortcuts(\n        [("calc.py", '
    'prod_old, prod_new), ("tests/test_calc.py", test_src, test_src)]\n    )\n '
    "   assert findings == []\n\n\ndef test_fixture_return_literal_is_a_known_li"
    'mit() -> None:\n    test_src = (\n        "import pytest\\n\\n"\n        "@py'
    'test.fixture\\n"\n        "def code():\\n    return \'magic\'\\n\\n"\n        "d'
    'ef test_f(code):\\n    assert f(code) == 1\\n"\n    )\n    prod_old = "def f'
    "(v):\\n    return v\\n\"\n    prod_new = \"def f(v):\\n    if v == 'magic':\\n "
    "       return 'x'\\n    return v\\n\"\n    findings = detect_shortcuts(\n    "
    '    [("calc.py", prod_old, prod_new), ("tests/test_calc.py", test_src, t'
    "est_src)]\n    )\n    assert findings == []\n\n\ndef test_findings_are_sorted"
    "_by_path_line_kind() -> None:\n    findings = detect_shortcuts(\n        ["
    '\n            ("b.py", "def f():\\n    return 1\\n", "def f():\\n    pass\\n"'
    '),\n            ("a.py", "def g():\\n    return 1\\n", "def g():\\n    ...\\n'
    '"),\n        ]\n    )\n    assert [(f.path, f.line, f.kind) for f in findin'
    "gs] == sorted(\n        (f.path, f.line, f.kind) for f in findings\n    )\n"
    '    assert [f.path for f in findings] == ["a.py", "b.py"]\n'
)


def test_commit_8b11349_test_move_is_clean() -> None:
    assert (
        detect_shortcuts(
            [
                ("backend/tests/review/test_shortcut_detector.py", OLD, None),
                ("backend/tests/changes/test_shortcut_detector.py", None, NEW),
            ]
        )
        == []
    )
