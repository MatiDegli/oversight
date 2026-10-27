"""Retained helpers, validation usage and per-helper analysis cost."""

from collections import Counter

import pytest

from oversight.changes import ShortcutKind, detect_shortcuts
from oversight.changes import _shortcuts_helpers as helpers
from oversight.changes._shortcuts_common import Change, FuncNode, Source


@pytest.mark.parametrize(
    "statement,call,name",
    [
        ("import subprocess", "subprocess.run(['x'])", "run"),
        ("import os.path", "os.path.join('a', 'b')", "join"),
    ],
)
def test_unrelated_import_cannot_cancel_a_removed_helper_call(
    statement: str, call: str, name: str
) -> None:
    old = (
        f"{statement}\ndef {name}():\n    assert f() == 3\n"
        f"def test_f():\n    {name}()\n    {call}\n"
    )
    new = old.replace(f"    {name}()\n", "")
    result = detect_shortcuts([("tests/test_calc.py", old, new)])
    assert result.status == "findings" and not result.reasons
    assert {f.kind for f in result} == {ShortcutKind.ASSERTION_LOOSENED}
    assert "a call to an asserting helper was removed" in result[0].message


@pytest.mark.parametrize("validation", ["model_validate", "model_validate_json"])
@pytest.mark.parametrize(
    "statement",
    ["return VALUE", "body = VALUE\n    return body", "consume(VALUE)", "VALUE.error.code"],
)
def test_used_validation_result_makes_a_helper_a_check(validation: str, statement: str) -> None:
    body = statement.replace("VALUE", f"Event.{validation}(RAW)")
    old = f"def event():\n    {body}\ndef test_f():\n    event()\n"
    result = detect_shortcuts([("tests/test_calc.py", old, old.replace("    event()", "    pass"))])
    assert result.status == "findings"
    assert {f.kind for f in result} == {ShortcutKind.ASSERTION_LOOSENED}


def test_assigned_validation_is_a_documented_syntactic_tradeoff() -> None:
    import oversight.changes._shortcut_detector as detector

    assert "assignment counts even" in (detector.__doc__ or "")
    old = "def event():\n    unused = Event.model_validate(RAW)\ndef test_f():\n    event()\n"
    result = detect_shortcuts([("tests/test_calc.py", old, "def test_f():\n    pass\n")])
    assert {f.kind for f in result} == {ShortcutKind.ASSERTION_LOOSENED}


@pytest.mark.parametrize("extracted", [False, True])
def test_validation_inventory_is_built_once_per_helper(
    monkeypatch: pytest.MonkeyPatch, extracted: bool
) -> None:
    inventories: Counter[FuncNode] = Counter()
    original = helpers._validations

    def inventory(func: FuncNode, source: Source) -> Counter[str]:
        inventories[func] += 1
        return original(func, source)

    monkeypatch.setattr(helpers, "_validations", inventory)
    definitions = "".join(f"def check_{i}():\n    assert f({i}) == 3\n" for i in range(9))
    tests = "".join(f"def test_{i}():\n    check_0()\n    check_0()\n" for i in range(12))
    new = "from helpers import check_0\n" + tests if extracted else definitions + tests
    changes: list[Change] = [("tests/test_calc.py", definitions + tests, new + "# edit\n")]
    if extracted:
        changes.append(("tests/helpers.py", None, definitions))
    result = detect_shortcuts(changes)
    assert result.status == "no_facts" and not result
    assert len(inventories) == (10 if extracted else 9)
    assert set(inventories.values()) == {1}


def test_old_validation_cannot_make_a_bare_imported_validation_a_check() -> None:
    definitions = [
        "def check():\n    assert f()\n    Event.model_validate(RAW)\n",
        "def check():\n    assert f()\n    Other.model_validate(RAW)\n",
    ]
    changes: list[Change] = [
        (
            f"tests/test_{i}.py",
            definition + "def test_f():\n    check()\n",
            "from helpers import check\ndef test_f():\n    check()\n",
        )
        for i, definition in enumerate(definitions)
    ]
    changes.append(("tests/helpers.py", None, "def check():\n    Event.model_validate(RAW)\n"))
    result = detect_shortcuts(changes)
    assert result.status == "findings"
    assert {f.path for f in result} == {"tests/test_0.py", "tests/test_1.py"}
