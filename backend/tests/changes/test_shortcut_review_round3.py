"""Recall and uncertainty regressions from Sonnet's final gate."""

import time

import pytest

from oversight.changes import ShortcutKind, detect_shortcuts


def kinds(old: str, new: str, path: str = "tests/test_calc.py") -> set[str]:
    return {f.kind for f in detect_shortcuts([(path, old, new)])}


@pytest.mark.parametrize("rows", ["[(1, 3)]", "[]"])
@pytest.mark.parametrize("names, check", [("x,expected", "f(x) == expected"), ("x", "f(x)")])
def test_parameter_rows_removed(rows: str, names: str, check: str) -> None:
    before = "[(1, 3), (2, 4)]" if "," in names else "[1, 2]"
    after = rows if "," in names else ("[1]" if rows != "[]" else rows)
    old = (
        f"import pytest\n@pytest.mark.parametrize('{names}', {before})\n"
        f"def test_f(x, expected=3):\n    assert {check}\n"
    )
    assert "assertion_loosened" in kinds(old, old.replace(before, after))


@pytest.mark.parametrize("column", ["want", "result"])
def test_expected_column_from_assertion(column: str) -> None:
    old = (
        f"import pytest\n@pytest.mark.parametrize('a,b,{column}', [(1,2,3)])\n"
        f"def test_f(a,b,{column}):\n    assert add(a,b) == {column}\n"
    )
    assert "expected_value_changed" in kinds(old, old.replace("(1,2,3)", "(1,2,4)"))


@pytest.mark.parametrize("destination", ["tests/x_checks.py", "tests/helpers.py", "helpers.py"])
@pytest.mark.parametrize("keep_origin", [False, True])
def test_move_must_remain_collected(destination: str, keep_origin: bool) -> None:
    old = "def test_f():\n    assert f() == 3\n"
    changes = [("tests/test_calc.py", old, "" if keep_origin else None), (destination, None, old)]
    assert "test_removed" in {f.kind for f in detect_shortcuts(changes)}


@pytest.mark.parametrize(
    "body",
    [
        "self.skipTest('later')\n        assert f() == 3",
        "raise unittest.SkipTest('later')\n        assert f() == 3",
        "raise unittest.SkipTest\n        assert f() == 3",
        "pytest.importorskip('optional')\n        assert f() == 3",
    ],
)
def test_skip_calls_and_raises(body: str) -> None:
    old = (
        "import unittest\nimport pytest\nclass TestCalc(unittest.TestCase):\n"
        "    def test_f(self):\n        assert f() == 3\n"
    )
    assert "skip_added" in kinds(old, old.replace("assert f() == 3", body))


@pytest.mark.parametrize("scope", ["module", "class", "function"])
def test_test_false_marker(scope: str) -> None:
    old = "class TestCalc:\n    def test_f(self):\n        assert f() == 3\n"
    new = {
        "module": "__test__ = False\n" + old,
        "class": old.replace("class TestCalc:\n", "class TestCalc:\n    __test__ = False\n"),
        "function": old + "TestCalc.test_f.__test__ = False\n",
    }[scope]
    assert "skip_added" in kinds(old, new)


@pytest.mark.parametrize(
    "new",
    [
        "collect_ignore = ['test_calc.py']\n",
        "collect_ignore_glob = ['test_*.py']\n",
        "import pytest\ndef pytest_collection_modifyitems(items):\n"
        "    for item in items:\n        item.add_marker(pytest.mark.skip)\n",
    ],
)
def test_conftest_collection_is_unknown(new: str) -> None:
    result = detect_shortcuts([("tests/conftest.py", "", new)])
    assert result and result.status == "unknown"
    assert any("collection" in reason.lower() for reason in result.reasons)


@pytest.mark.parametrize(
    "body",
    [
        "try:\n        assert f() == 3\n    except AssertionError:\n        pass",
        "with contextlib.suppress(AssertionError):\n        assert f() == 3",
        "if False:\n        assert f() == 3",
        "return\n    assert f() == 3",
    ],
)
def test_neutralized_assertion(body: str) -> None:
    old = "import contextlib\ndef test_f():\n    assert f() == 3\n"
    assert "assertion_loosened" in kinds(old, old.replace("assert f() == 3", body))


@pytest.mark.parametrize("edit", ["rename", "delete", "uncall"])
def test_asserting_helper_is_not_lost(edit: str) -> None:
    old = "def check():\n    assert f() == 3\ndef test_f():\n    check()\n"
    new = {
        "rename": old.replace("check", "verify").replace("assert f() == 3", "f()"),
        "delete": "def test_f():\n    f()\n",
        "uncall": old.replace("    check()", "    f()"),
    }[edit]
    assert "assertion_loosened" in kinds(old, new)


def test_helper_rename_with_checks_preserved_is_clean() -> None:
    old = "def check():\n    assert f() == 3\ndef test_f():\n    check()\n"
    assert detect_shortcuts([("tests/test_calc.py", old, old.replace("check", "verify"))]) == []


@pytest.mark.parametrize("replacement", ["x -= x - 1", "import os as x"])
def test_parameter_rebinding(replacement: str) -> None:
    old = "def test_f(x):\n    assert x == 1\n"
    assert "assertion_loosened" in kinds(
        old, old.replace("    assert", f"    {replacement}\n    assert")
    )


def test_plain_binding_replaced_with_tautology() -> None:
    old = "def test_f():\n    result = compute()\n    assert result == 73\n"
    assert "assertion_loosened" in kinds(old, old.replace("= compute()", "= 73"))


@pytest.mark.parametrize("before, after", [("3", "4"), ("compute(1)", "other(1)")])
def test_general_plain_binding_changes_are_a_documented_limit(before: str, after: str) -> None:
    import oversight.changes._shortcut_detector as detector

    assert "Plain binding value changes" in (detector.__doc__ or "")
    old = f"def test_f():\n    result = {before}\n    assert result == 73\n"
    assert detect_shortcuts([("test_calc.py", old, old.replace(f"= {before}", f"= {after}"))]) == []


@pytest.mark.parametrize("expected", ["expected", "g()"])
@pytest.mark.parametrize("before, after", [("abs=1e-9", "abs=1"), ("rel=1e-6", "rel=0.5")])
def test_dynamic_expected_tolerance_widened(expected: str, before: str, after: str) -> None:
    old = f"import pytest\ndef test_f():\n    assert f() == pytest.approx({expected}, {before})\n"
    assert "assertion_loosened" in kinds(old, old.replace(before, after))


@pytest.mark.parametrize("path", ["pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"])
def test_collection_config_unknown(path: str) -> None:
    result = detect_shortcuts([(path, "", "testpaths = other\n")])
    assert result and result.status == "unknown" and result.reasons


def test_parse_failure_is_unknown() -> None:
    result = detect_shortcuts([("tests/test_calc.py", "", "def broken(:")])
    assert result.status == "unknown" and result.reasons
    assert [f.kind for f in result] == [ShortcutKind.PARSE_ERROR]


@pytest.mark.parametrize("exception", [RecursionError, TimeoutError, MemoryError])
def test_analysis_failure_is_unknown(
    monkeypatch: pytest.MonkeyPatch, exception: type[Exception]
) -> None:
    import oversight.changes._shortcut_detector as detector

    def fail(*args: object, **kwargs: object) -> None:
        raise exception("analysis exhausted")

    monkeypatch.setattr(detector, "match_tests", fail)
    result = detect_shortcuts([("tests/test_calc.py", "", "def test_f():\n    assert ok\n")])
    assert result and result.status == "unknown"
    assert exception.__name__ in " ".join(result.reasons)


@pytest.mark.parametrize(
    "source",
    [
        "import pytest\n@pytest.mark.parametrize('x', cases())\ndef test_f(x):\n    assert x\n",
        "def test_f():\n    assert x\ndef test_f():\n    assert y\n",
        "import pytest\nm = pytest.mark\n@m.skip\ndef test_f():\n    assert x\n",
        "def test_f():\n    assert x\ntest_f = None\n",
    ],
)
def test_uncomparable_construct_unknown(source: str) -> None:
    result = detect_shortcuts([("tests/test_calc.py", "", source)])
    assert result and result.status == "unknown" and result.reasons


def test_renamed_test_matching_is_bounded() -> None:
    old = "\n".join(f"def test_old_{i}():\n    assert f({i}) == {i}\n" for i in range(300))
    new = old.replace("test_old_", "test_new_")
    started = time.monotonic()
    result = detect_shortcuts([("tests/test_calc.py", old, new)])
    assert time.monotonic() - started < 2
    assert result == []


def test_expired_analysis_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter(range(100))
    monkeypatch.setattr("oversight.changes._shortcuts_common.time.monotonic", ticks.__next__)
    result = detect_shortcuts(
        [("tests/test_calc.py", "", "def test_f():\n    assert f() == 3\n")], timeout=3
    )
    assert result and result.status == "unknown"
    assert "timeout" in " ".join(result.reasons).lower()


def test_changed_collection_exclusion_remains_unknown() -> None:
    result = detect_shortcuts(
        [("tests/conftest.py", "collect_ignore = ['a.py']", "collect_ignore = ['b.py']")]
    )
    assert result and result.status == "unknown" and result.reasons


def test_crossed_opaque_tolerances_are_unknown() -> None:
    old = "import pytest\ndef test_f(expected):\n    assert f() == pytest.approx(expected)\n"
    new = old.replace("approx(expected)", "approx(expected, abs=1e-9)")
    result = detect_shortcuts([("test_calc.py", old, new)])
    assert result and result.status == "unknown"
    assert "tolerances" in " ".join(result.reasons)


def test_deep_expression_is_unknown() -> None:
    expression = "+".join(["1"] * 300)
    result = detect_shortcuts([("test_calc.py", "", f"def test_f():\n    assert {expression}\n")])
    assert result and result.status == "unknown"
    assert "RecursionError" in " ".join(result.reasons)


def test_large_matching_pool_is_unknown() -> None:
    old = "\n".join(f"def test_old_{i}():\n    assert f({i})\n" for i in range(601))
    result = detect_shortcuts([("test_calc.py", old, old.replace("test_old_", "test_new_"))])
    assert result and result.status == "unknown"
    assert "cap" in " ".join(result.reasons)


def test_opaque_replacement_table_is_unknown_without_claiming_row_loss() -> None:
    old = "import pytest\n@pytest.mark.parametrize('x', [1, 2])\ndef test_f(x):\n    assert f(x)\n"
    result = detect_shortcuts([("test_calc.py", old, old.replace("[1, 2]", "cases()"))])
    assert result.status == "unknown" and result.reasons
    assert {f.kind for f in result} == {ShortcutKind.UNKNOWN}


def test_single_literal_case_can_be_inlined() -> None:
    old = (
        "import pytest\n@pytest.mark.parametrize('x', [1])\ndef test_f(x):\n    assert f(x) == 3\n"
    )
    new = "def test_f():\n    assert f(1) == 3\n"
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


def test_replacing_a_row_still_removes_the_old_case() -> None:
    old = "import pytest\n@pytest.mark.parametrize('x', [1, 2])\ndef test_f(x):\n    assert f(x)\n"
    assert "assertion_loosened" in kinds(old, old.replace("[1, 2]", "[1, 3]"))
