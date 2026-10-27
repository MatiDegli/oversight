"""Extraction targets must retain checks; named table lookups stay bounded."""

import ast
import time

import pytest

from oversight.changes import ShortcutKind, detect_shortcuts
from oversight.changes._shortcuts_common import Source

OLD = "def check():\n    assert f() == 3\ndef test_f():\n    check()\n"


@pytest.mark.parametrize("body", ["return None", "assert True", "f()", "pass"])
@pytest.mark.parametrize(
    "statement,call",
    [
        ("from helpers import check", "check()"),
        ("from helpers import check as verify", "verify()"),
        ("import helpers", "helpers.check()"),
        ("from .helpers import check", "check()"),
    ],
)
def test_extraction_to_helper_without_checks_is_a_fact(
    body: str, statement: str, call: str
) -> None:
    result = detect_shortcuts(
        [
            ("tests/test_calc.py", OLD, f"{statement}\ndef test_f():\n    {call}\n"),
            ("tests/helpers.py", None, f"def check():\n    {body}\n"),
        ]
    )
    assert result.status == "findings"
    assert ShortcutKind.ASSERTION_LOOSENED in {f.kind for f in result}
    assert "removed or replaced" in " ".join(f.message for f in result)
    assert "call to an asserting helper was removed" in " ".join(f.message for f in result)


@pytest.mark.parametrize("removed_call", [False, True])
def test_invisible_extraction_is_unknown_without_hiding_removed_calls(removed_call: bool) -> None:
    old = OLD + "def test_g():\n    check()\n"
    new = "from helpers import check\ndef test_f():\n    check()\ndef test_g():\n    "
    new += "f()\n" if removed_call else "check()\n"
    result = detect_shortcuts([("tests/test_calc.py", old, new)])
    assert result.status == "unknown" and result.reasons
    assert "target is not visible" in " ".join(result.reasons)
    facts = {f.kind for f in result} - {ShortcutKind.UNKNOWN}
    assert facts == ({ShortcutKind.ASSERTION_LOOSENED} if removed_call else set())


def test_unrelated_same_named_helper_does_not_verify_imported_target() -> None:
    result = detect_shortcuts(
        [
            ("tests/test_calc.py", OLD, "from checks import check\ndef test_f():\n    check()\n"),
            ("tests/helpers.py", None, "def check():\n    assert f() == 3\n"),
        ]
    )
    assert result.status == "unknown" and "target is not visible" in " ".join(result.reasons)


@pytest.mark.parametrize(
    "setup,names,reason",
    [
        ("def configure():\n    global CASES\n", "'x,expected'", "global declaration"),
        ("NAMES = 'x,expected'\n", "NAMES", "non-literal parameter names"),
    ],
)
def test_reachable_named_table_unknown_reasons(setup: str, names: str, reason: str) -> None:
    source = (
        "import pytest\nCASES = [(1, 3)]\n" + setup + f"@pytest.mark.parametrize({names}, CASES)\n"
        "def test_f(x,expected):\n    assert f(x) == expected\n"
    )
    result = detect_shortcuts([("tests/test_calc.py", source, source + "# comment\n")])
    assert result.status == "unknown" and reason in " ".join(result.reasons)


@pytest.mark.parametrize("count", [150, 200, 590])
def test_many_method_decorators_share_the_scope_index(count: int) -> None:
    source = "import pytest\nCASES = [(1, 3)]\nclass TestCalc:\n" + "".join(
        f"    @pytest.mark.parametrize('x,expected', CASES)\n"
        f"    def test_f_{i}(self,x,expected):\n        assert f(x) == expected\n"
        for i in range(count)
    )
    start = time.monotonic()
    result = detect_shortcuts([("tests/test_calc.py", source, source + "# comment\n")], timeout=2)
    assert time.monotonic() - start < 3
    assert result.status == "no_facts" and not result


def test_table_index_observes_the_analysis_deadline() -> None:
    source = Source("CASES = [(1, 3)]\n", deadline=0)
    with pytest.raises(TimeoutError, match="analysis timeout"):
        _ = source.table_shadows
    node = next(n for n in ast.walk(source.tree) if isinstance(n, ast.Name))
    with pytest.raises(TimeoutError, match="analysis timeout"):
        source.table_value(node)


@pytest.mark.parametrize("validation", ["model_validate", "model_validate_json"])
def test_extracted_helper_retains_explicit_schema_validation(validation: str) -> None:
    old = (
        "def check(response):\n    assert hasattr(response, 'json')\n"
        f"    body = ErrorResponse.{validation}(response.json())\n"
        "    return body.error.code\ndef test_f():\n    assert check(response) == 'invalid'\n"
    )
    result = detect_shortcuts(
        [
            (
                "tests/test_calc.py",
                old,
                "from helpers import check\ndef test_f():\n"
                "    assert check(response) == 'invalid'\n",
            ),
            (
                "tests/helpers.py",
                None,
                f"def check(response):\n"
                f"    return ErrorResponse.{validation}(response.json()).error.code\n",
            ),
        ]
    )
    assert result.status == "no_facts" and not result


def test_table_index_checks_deadline_during_the_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Source("CASES = [(1, 3)]\n", deadline=4)
    ticks = iter(range(10))
    with monkeypatch.context() as patch:
        patch.setattr("oversight.changes._shortcuts_common.time.monotonic", ticks.__next__)
        with pytest.raises(TimeoutError, match="analysis timeout"):
            _ = source.table_shadows


def test_table_lookups_reuse_the_definition_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Source(
        "CASES = [(1, 3)]\nclass TestCalc:\n"
        "    @pytest.mark.parametrize('x,expected', CASES)\n"
        "    def test_f(self,x,expected):\n        assert f(x) == expected\n"
    )

    def unexpected_rescan(*args: object, **kwargs: object) -> None:
        pytest.fail("Table lookup rescanned a definition's bindings")

    monkeypatch.setattr("oversight.changes._shortcuts_common._scope_bindings", unexpected_rescan)
    nodes = [n for n in ast.walk(source.tree) if isinstance(n, ast.Name) and n.id == "CASES"]
    for _ in range(3):
        for node in nodes:
            assert source.table_value(node) is source.tables.values["CASES"]
