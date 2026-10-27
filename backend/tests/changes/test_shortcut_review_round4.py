"""Named-table comparisons, helper extraction and resource bounds from re-review."""

import ast

import pytest

from oversight.changes import ShortcutKind, detect_shortcuts
from oversight.changes._shortcuts_asserts import (
    _compare_assertions,
    _compare_raises,
    _unmatched_assertions,
)
from oversight.changes._shortcuts_common import Source
from oversight.changes._shortcuts_tests import _Case, _collect_parametrize, _compare_rows

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
    result = detect_shortcuts(
        [
            ("tests/test_calc.py", old, new),
            ("tests/helpers.py", None, "def check():\n    assert f() == 3\n"),
        ]
    )
    assert {f.kind for f in result} == {ShortcutKind.ASSERTION_LOOSENED}
    assert "a call to an asserting helper was removed" in result[0].message


@pytest.mark.parametrize("shape", ["collect", "outer", "pair", "inner"])
def test_row_loops_cross_deadline_after_reads(monkeypatch: pytest.MonkeyPatch, shape: str) -> None:
    source = Source(
        "import pytest\n@pytest.mark.parametrize('x,expected', "
        "[(1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7)])\n"
        "def test_f(x,expected):\n    assert f(x) == expected\n"
    )
    cases = [_Case((str(i),), (str(i),), str(i), 1) for i in range(6)]
    ticks = iter(range(100))
    monkeypatch.setattr("oversight.changes._shortcuts_common.time.monotonic", ticks.__next__)
    with pytest.raises(TimeoutError, match="analysis timeout"):
        if shape == "collect":
            _collect_parametrize(source, deadline=3)
        elif shape == "outer":
            # No candidates: only the two outer loops read the clock.
            _compare_rows("test_calc.py", cases, [], deadline=3)
        elif shape == "pair":
            # Six cancellation reads, then expire during the pairing loop.
            _compare_rows("test_calc.py", cases, [], deadline=9)
        else:
            _compare_rows("test_calc.py", cases[:1], cases[1:], deadline=3)


@pytest.mark.parametrize("shape", ["outer", "inner"])
def test_assertion_matching_crosses_deadline_after_reads(
    monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    count = 6 if shape == "outer" else 1
    old = Source("def test_f():\n" + "    assert f() == 0\n" * count)
    new = Source(
        "def test_f():\n" + ("    pass\n" if shape == "outer" else "    assert g() == 1\n" * 6)
    )
    # Isolate matching from the later wording pass: a later timeout must not
    # conceal removal of either matching-loop check.
    monkeypatch.setattr(
        "oversight.changes._shortcuts_asserts.cancel_reworded", lambda a, b, **kw: (a, b)
    )
    ticks = iter(range(100))
    monkeypatch.setattr("oversight.changes._shortcuts_common.time.monotonic", ticks.__next__)
    with pytest.raises(TimeoutError, match="analysis timeout"):
        _unmatched_assertions(old.tests["test_f"], new.tests["test_f"], old, new, 3)


@pytest.mark.parametrize("shape", ["outer", "equivalent", "forward", "reverse"])
def test_assertion_comparison_crosses_deadline_after_reads(
    monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    old = Source("def test_f():\n" + "    assert f() == 0\n" * 6)
    new = Source("def test_f():\n" + "    assert g() == 1\n" * 6)
    left: list[ast.Assert | ast.Call] = [
        n for n in old.tests["test_f"].body if isinstance(n, ast.Assert)
    ]
    right: list[ast.Assert | ast.Call] = [
        n for n in new.tests["test_f"].body if isinstance(n, ast.Assert)
    ]
    monkeypatch.setattr("oversight.changes._shortcuts_asserts._approx_match", lambda *args: None)
    if shape == "equivalent":

        def unexpected_pair(*args: object) -> None:
            pytest.fail("Comparison reached pairing before the equivalence loop timed out")

        monkeypatch.setattr("oversight.changes._shortcuts_asserts._check_pair", unexpected_pair)
    else:

        def no_pair(path: str, before: object, *args: object) -> None:
            if shape == "forward" and before in right:
                pytest.fail("Comparison reached reverse pairing before the forward loop timed out")

        monkeypatch.setattr("oversight.changes._shortcuts_asserts._check_pair", no_pair)
        monkeypatch.setattr(
            "oversight.changes._shortcuts_asserts.within_deadline", lambda items, _: items
        )
    if shape == "outer":
        right = []
    else:
        left = left[:1]
    ticks = iter(range(100))
    monkeypatch.setattr("oversight.changes._shortcuts_common.time.monotonic", ticks.__next__)
    with pytest.raises(TimeoutError, match="analysis timeout"):
        _compare_assertions("test_calc.py", left, right, old, new, 9 if shape == "reverse" else 3)


@pytest.mark.parametrize("shape", ["outer", "inner"])
def test_raises_comparison_crosses_deadline_after_reads(
    monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    before = "    with pytest.raises(ValueError):\n        f()\n"
    after = "    with pytest.raises(TypeError):\n        f()\n"
    old = Source("import pytest\ndef test_f():\n" + before * (6 if shape == "outer" else 1))
    new = Source(
        "import pytest\ndef test_f():\n" + ("    pass\n" if shape == "outer" else after * 6)
    )
    ticks = iter(range(100))
    monkeypatch.setattr("oversight.changes._shortcuts_common.time.monotonic", ticks.__next__)
    with pytest.raises(TimeoutError, match="analysis timeout"):
        _compare_raises("test_calc.py", old.tests["test_f"], new.tests["test_f"], old, new, 3)


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
