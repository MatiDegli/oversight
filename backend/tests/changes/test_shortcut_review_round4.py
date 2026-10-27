"""Named-table comparisons, helper extraction and resource bounds from re-review."""

import time

import pytest

from oversight.changes import ShortcutKind, detect_shortcuts

TEST = (
    "@pytest.mark.parametrize('x,expected', CASES)\n"
    "def test_f(x, expected):\n    assert f(x) == expected\n"
)
OLD = "import pytest\nCASES = [(1, 3), (2, 4)]\n" + TEST


@pytest.mark.parametrize(
    "replacement,kind",
    [
        ("[]", ShortcutKind.ASSERTION_LOOSENED),
        ("[(1, 3)]", ShortcutKind.ASSERTION_LOOSENED),
        ("[(1, 3), (2, 5)]", ShortcutKind.EXPECTED_VALUE_CHANGED),
    ],
)
@pytest.mark.parametrize("annotated", [False, True])
def test_named_literal_rows_are_compared(
    replacement: str, kind: ShortcutKind, annotated: bool
) -> None:
    old = OLD.replace("CASES =", "CASES: list =") if annotated else OLD
    result = detect_shortcuts([("test_calc.py", old, old.replace("[(1, 3), (2, 4)]", replacement))])
    assert result.status == "findings" and kind in {f.kind for f in result}
    assert not result.reasons


def test_named_tuple_rows_are_compared() -> None:
    old = OLD.replace("[(1, 3), (2, 4)]", "((1, 3), (2, 4))")
    result = detect_shortcuts([("test_calc.py", old, old.replace("(2, 4)", "(2, 5)"))])
    assert {f.kind for f in result} == {ShortcutKind.EXPECTED_VALUE_CHANGED}


def test_named_pytest_param_rows_are_compared() -> None:
    old = OLD.replace("(2, 4)", "pytest.param(2, 4, id='second')")
    result = detect_shortcuts([("test_calc.py", old, old.replace("param(2, 4", "param(2, 5"))])
    assert {f.kind for f in result} == {ShortcutKind.EXPECTED_VALUE_CHANGED}


@pytest.mark.parametrize("expression", ["CASES[:1]", "CASES.pop()"])
def test_computed_table_argument_is_unknown(expression: str) -> None:
    result = detect_shortcuts(
        [("test_calc.py", OLD, OLD.replace("', CASES)", f"', {expression})"))]
    )
    assert result.status == "unknown" and result.reasons
    assert {f.kind for f in result} == {ShortcutKind.UNKNOWN}
    assert "parameter table" in " ".join(result.reasons)


@pytest.mark.parametrize(
    "setup,reason",
    [
        ("CASES = [(1, 3), (2, 4)]\nCASES.pop()\n", "mutated"),
        ("CASES = [(1, 3), (2, 4)]\nCASES[1] = (2, 5)\n", "mutated"),
        ("CASES = [(1, 3), (2, 4)]\nCASES = []\n", "rebound"),
        ("CASES = [(1, 3), (2, 4)]\nCASES = CASES[:1]\n", "rebound"),
        ("CASES = [(1, 3), (2, 4)]\nCASES += [(3, 5)]\n", "rebound"),
        ("CASES = build_cases()\n", "computed"),
        ("CASES = [*build_cases()]\n", "computed"),
        ("from data import CASES\n", "imported"),
        ("CASES = [(1, 3), (2, 4)]\nshrink(CASES)\n", "mutate"),
        ("CASES = [(1, 3), (2, 4)]\nother = CASES\nother.pop()\n", "aliased"),
    ],
)
def test_opaque_named_table_never_means_no_facts(setup: str, reason: str) -> None:
    result = detect_shortcuts([("test_calc.py", OLD, "import pytest\n" + setup + TEST)])
    assert result.status == "unknown" and result.reasons
    assert reason in " ".join(result.reasons)
    assert {f.kind for f in result} == {ShortcutKind.UNKNOWN}


@pytest.mark.parametrize("value", ["build_cases()", "other_cases()"])
def test_stable_decorator_with_opaque_binding_is_unknown(value: str) -> None:
    old = OLD.replace("[(1, 3), (2, 4)]", "build_cases()")
    new = old.replace("build_cases()", value) + "# unrelated edit\n"
    result = detect_shortcuts([("test_calc.py", old, new)])
    assert result.status == "unknown" and "computed" in " ".join(result.reasons)


@pytest.mark.parametrize("moved", [False, True])
def test_opaque_table_in_renamed_or_moved_test_is_still_a_comparison(moved: bool) -> None:
    old = OLD.replace("[(1, 3), (2, 4)]", "build_cases()")
    new = old.replace("test_f", "test_renamed").replace("build_cases()", "other_cases()")
    changes = (
        [("test_calc.py", old, None), ("test_moved.py", None, new)]
        if moved
        else [("test_calc.py", old, new)]
    )
    result = detect_shortcuts(changes)
    assert result.status == "unknown" and "computed" in " ".join(result.reasons)


@pytest.mark.parametrize("before", [None, "def test_existing():\n    assert ok\n"])
@pytest.mark.parametrize("setup", ["CASES = [(1, 3)]\n", "from data import CASES\n"])
def test_pure_addition_with_named_table_has_no_comparison(before: str | None, setup: str) -> None:
    result = detect_shortcuts(
        [("test_calc.py", before, (before or "") + "import pytest\n" + setup + TEST)]
    )
    assert result == [] and result.status == "no_facts" and not result.reasons


def test_unchanged_literal_table_does_not_report_unknown() -> None:
    result = detect_shortcuts([("test_calc.py", OLD, OLD + "# unrelated edit\n")])
    assert result == [] and result.status == "no_facts"


@pytest.mark.parametrize("shadowed", [False, True])
def test_method_decorator_respects_its_enclosing_table_binding(shadowed: bool) -> None:
    table = "    CASES = make_cases()\n" if shadowed else ""
    old = (
        "import pytest\nCASES = [(1, 3), (2, 4)]\nclass TestCalc:\n"
        + table
        + "    @pytest.mark.parametrize('x,expected', CASES)\n"
        "    def test_f(self,x,expected):\n        assert f(x) == expected\n"
    )
    result = detect_shortcuts([("test_calc.py", old, old.replace("(2, 4)", "(2, 5)"))])
    if shadowed:
        assert result.status == "unknown" and "shadowed" in " ".join(result.reasons)
    else:
        assert {f.kind for f in result} == {ShortcutKind.EXPECTED_VALUE_CHANGED}


@pytest.mark.parametrize("table", ["list(CASES)", "range(len(CASES))", "load_cases()", "range(5)"])
def test_new_test_over_bound_or_transformed_table_has_no_earlier_rows(table: str) -> None:
    setup = "import pytest\nfrom data import CASES, load_cases\n"
    new = setup + TEST.replace("', CASES)", f"', {table})")
    result = detect_shortcuts([("test_calc.py", None, new)])
    assert result == [] and result.status == "no_facts"


@pytest.mark.parametrize("path", [".pytest.ini", "pytest.toml", ".pytest.toml"])
def test_current_pytest_collection_configs_are_unknown(path: str) -> None:
    result = detect_shortcuts([(path, "", "testpaths = other\n")])
    assert result.status == "unknown" and result.reasons
    assert "collection configuration" in " ".join(result.reasons)


@pytest.mark.parametrize(
    "statement,call",
    [
        ("from helpers import check", "check()"),
        ("from helpers import check as verify", "verify()"),
        ("import helpers", "helpers.check()"),
    ],
)
def test_moved_helper_still_imported_and_called_is_not_removed(statement: str, call: str) -> None:
    old = "def check():\n    assert f() == 3\ndef test_f():\n    check()\n"
    new = f"{statement}\ndef test_f():\n    {call}\n"
    result = detect_shortcuts(
        [
            ("tests/test_calc.py", old, new),
            ("tests/helpers.py", None, "def check():\n    assert f() == 3, 'diagnostic'\n"),
        ]
    )
    assert result == [] and result.status == "no_facts"


def test_extraction_does_not_hide_another_removed_helper_call() -> None:
    old = (
        "def check():\n    assert f() == 3\n"
        "def test_f():\n    check()\ndef test_g():\n    check()\n"
    )
    new = "from helpers import check\ndef test_f():\n    check()\ndef test_g():\n    f()\n"
    result = detect_shortcuts([("tests/test_calc.py", old, new)])
    assert {f.kind for f in result} == {ShortcutKind.ASSERTION_LOOSENED}
    assert "a call to an asserting helper was removed" in result[0].message


@pytest.mark.parametrize("shape", ["rows", "assertions"])
def test_comparison_loops_respect_two_second_deadline(shape: str) -> None:
    if shape == "rows":
        rows = ",".join(f"({i}, {i})" for i in range(6000))
        changed = ",".join(f"({i}, {i + 1})" for i in range(6000))
        template = (
            "import pytest\n@pytest.mark.parametrize('x,expected', [{}])\n"
            "def test_f(x,expected):\n    assert f(x) == expected\n"
        )
        old, new = template.format(rows), template.format(changed)
    else:
        old = "def test_f():\n" + "".join(f"    assert f({i}) == {i}\n" for i in range(2000))
        new = "def test_f():\n" + "".join(f"    assert f({i}) == {i + 1}\n" for i in range(2000))
    started = time.monotonic()
    result = detect_shortcuts([("test_calc.py", old, new)], timeout=2)
    elapsed = time.monotonic() - started
    assert result.status == "unknown" and "timeout" in " ".join(result.reasons).lower()
    assert 1.9 <= elapsed < 3, (shape, elapsed)


def test_two_megabyte_source_cap_has_its_own_unknown_reason() -> None:
    result = detect_shortcuts([("test_calc.py", None, "#" * 2_000_001)])
    assert result.status == "unknown" and result.reasons
    assert "source size cap (2 MB)" in " ".join(result.reasons)


def test_parser_recursion_has_its_own_unknown_reason() -> None:
    expression = "+".join(["1"] * 5000)
    result = detect_shortcuts([("test_calc.py", None, f"def test_f():\n    assert {expression}\n")])
    assert result.status == "unknown" and result.reasons
    assert "RecursionError while parsing this file" in " ".join(result.reasons)


def test_four_hundred_pair_cap_has_its_own_unknown_reason() -> None:
    old = "".join(f"def test_old_{i}():\n    assert f({i}) == {i}\n" for i in range(21))
    new = "".join(f"def test_new_{i}():\n    assert g({i}) == {i + 1}\n" for i in range(21))
    result = detect_shortcuts([("test_calc.py", old, new)])
    assert result.status == "unknown" and result.reasons
    assert "comparison cap (400 pairs)" in " ".join(result.reasons)


def test_add_marker_without_collection_hook_has_its_own_unknown_reason() -> None:
    source = "import pytest\nitem.add_marker(pytest.mark.skip)\n"
    result = detect_shortcuts([("tests/conftest.py", "", source)])
    assert result.status == "unknown" and result.reasons
    assert "collection markers cannot be compared" in " ".join(result.reasons)
    assert "collection hook" not in " ".join(result.reasons)
