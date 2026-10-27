"""Before/after regressions from the third shortcut-detector review."""

import ast

import pytest

from oversight.changes import ShortcutKind, detect_shortcuts


@pytest.mark.parametrize(
    "stub", ["pass", "...", "raise NotImplementedError", "# TODO: later\n    return 1"]
)
def test_existing_stub_is_not_introduced(stub: str) -> None:
    old = f"def f():\n    {stub}\n"
    new = old.replace("def f():", "def f():\n    value = 2")
    assert detect_shortcuts([("calc.py", old, new)]) == []


def test_pass_todo_is_one_stub_per_line() -> None:
    old = "def f():\n    return 1\n"
    new = "def f():\n    pass  # TODO: implement\n"
    findings = detect_shortcuts([("calc.py", old, new)])
    assert [(f.kind, f.line) for f in findings] == [(ShortcutKind.STUB_LEFT, 2)]


@pytest.mark.parametrize(
    "source",
    [
        "from typing import Protocol as P\nclass API(P):\n    def f(self): ...\n",
        "from typing import overload as ov\n@ov\ndef f(x: int): ...\n",
        "from abc import abstractmethod as am\n@am\ndef f():\n    raise NotImplementedError\n",
        "from abc import ABC as Base\nclass API(Base):\n    def f(self): pass\n",
        "import abc\nclass API(metaclass=abc.ABCMeta):\n    def f(self): ...\n",
        "from typing import Protocol\nclass API(Protocol[int]):\n    def f(self): ...\n",
        "from abc import ABC\nclass Base(ABC): ...\nclass API(Base):\n    def f(self): pass\n",
    ],
)
def test_interface_stubs_are_clean(source: str) -> None:
    assert detect_shortcuts([("api.py", None, source)]) == []


@pytest.mark.parametrize(
    "before, after",
    [
        ("3, rel=1e-2", "3, rel=0, abs=1e-9"),
        ("3", "3, abs=1e-9"),
        ("3, rel=1e-2", "3, rel=1e-3, abs=1e-9"),
        ("0", "0, rel=1e-2"),
        ("-3, rel=1e-2", "-3, rel=0, abs=1e-9"),
        ("3, rel=1e-2, abs=1e-9", "3, rel=1e-2, abs=1e-8"),
        ("[3, -3], rel=1e-2", "[3, -3], rel=0, abs=1e-9"),
    ],
)
def test_approx_effective_tolerance_not_wider(before: str, after: str) -> None:
    old = f"import pytest\ndef test_f():\n    assert f() == pytest.approx({before})\n"
    new = old.replace(f"approx({before})", f"approx({after})")
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


@pytest.mark.parametrize(
    "before, after",
    [
        ("3", "3, abs=1e-2"),
        ("0", "0, abs=1e-9"),
        ("-3", "-3, rel=1e-2"),
        ("[3, 0]", "[3, 0], rel=1e-2"),
        ("{'x': 3}", "{'x': 3}, abs=1e-2"),
    ],
)
def test_approx_effective_tolerance_wider(before: str, after: str) -> None:
    old = f"import pytest\ndef test_f():\n    assert f() == pytest.approx({before})\n"
    new = old.replace(f"approx({before})", f"approx({after})")
    assert [f.kind for f in detect_shortcuts([("test_calc.py", old, new)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]


def test_test_move_with_import_module_change_is_clean() -> None:
    old = "from before.calc import add as op\ndef test_add():\n    assert op(1, 2) == 3\n"
    new = old.replace("before.calc", "after.math")
    assert (
        detect_shortcuts([("tests/test_old.py", old, None), ("tests/test_new.py", None, new)]) == []
    )


def test_move_does_not_hide_changed_imported_function() -> None:
    old = "from calc import add as op\ndef test_add():\n    assert op(1, 2) == 3\n"
    new = old.replace("import add", "import sub")
    assert [
        f.kind
        for f in detect_shortcuts(
            [("tests/test_old.py", old, None), ("tests/test_new.py", None, new)]
        )
    ] == [ShortcutKind.TEST_REMOVED]


@pytest.mark.parametrize("method", ["assertEqual", "assertTrue", "assertIn", "assertAlmostEqual"])
@pytest.mark.parametrize("message", [", 'diagnostic'", ", msg='diagnostic'"])
def test_unittest_diagnostic_message_is_clean(method: str, message: str) -> None:
    args = "f()" if method == "assertTrue" else "f(), 3"
    if method == "assertAlmostEqual":
        args += ", None"
    old = (
        "import unittest\nclass TestCalc(unittest.TestCase):\n"
        f"    def test_f(self):\n        self.{method}({args})\n"
    )
    new = old.replace(f"({args})", f"({args}{message})")
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


def test_extracted_call_used_once_in_assertion_is_clean() -> None:
    old = "def test_f():\n    assert f(1) == 3\n"
    new = "def test_f():\n    result = f(1)\n    assert result == 3\n"
    assert detect_shortcuts([("test_calc.py", old, new)]) == []
    assert detect_shortcuts([("test_calc.py", new, old)]) == []


def test_extraction_with_intervening_setup_is_clean() -> None:
    old = "def test_f():\n    label = 'case'\n    assert f(1) == 3, label\n"
    new = "def test_f():\n    result = f(1)\n    label = 'case'\n    assert result == 3, label\n"
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


def test_whitespace_edit_does_not_introduce_existing_except_pass() -> None:
    old = "def f():\n    try:\n        g()\n    except ValueError:\n        pass\n"
    new = old.replace("g()", "g(  )")
    assert detect_shortcuts([("calc.py", old, new)]) == []


@pytest.mark.parametrize("reverse", [False, True])
def test_splitting_conjunction_is_clean(reverse: bool) -> None:
    joined = "def test_f():\n    assert a and b and c\n"
    split = "def test_f():\n    assert a\n    assert b\n    assert c\n"
    old, new = (split, joined) if reverse else (joined, split)
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


def test_split_conjunction_still_detects_lost_operand() -> None:
    old = "def test_f():\n    assert a and b\n"
    new = "def test_f():\n    assert a\n"
    assert [f.kind for f in detect_shortcuts([("test_calc.py", old, new)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]


@pytest.mark.parametrize("mark", ["skip", "xfail", "skip(reason='slow')"])
def test_param_skip_marker_added(mark: str) -> None:
    old = (
        "import pytest\n@pytest.mark.parametrize('x', [pytest.param(2)])\n"
        "def test_f(x):\n    assert f(x) == 3\n"
    )
    new = old.replace("pytest.param(2)", f"pytest.param(2, marks=pytest.mark.{mark})")
    assert [f.kind for f in detect_shortcuts([("test_calc.py", old, new)])] == [
        ShortcutKind.SKIP_ADDED
    ]
    assert detect_shortcuts([("test_calc.py", new, new)]) == []


def test_no_collectable_test_helpers_in_detector_source() -> None:
    from pathlib import Path

    import oversight.changes._shortcuts_literals as module

    tree = ast.parse(Path(module.__file__).read_text())
    assert not any(isinstance(n, ast.FunctionDef) and n.name.startswith("test_") for n in tree.body)


@pytest.mark.parametrize(
    "prefix, old_check, new_check",
    [
        ("from pytest import approx as near\n", "near(3, rel=1e-6)", "near(3, rel=1e-2)"),
        ("import pytest as pt\n", "pt.approx(3)", "pt.approx(3, rel=1e-2)"),
    ],
)
def test_assertion_uses_scoped_import_resolver(prefix: str, old_check: str, new_check: str) -> None:
    old = f"{prefix}def test_f():\n    assert f() == {old_check}\n"
    new = old.replace(old_check, new_check)
    findings = detect_shortcuts([("test_calc.py", old, new)])
    assert [f.kind for f in findings] == [ShortcutKind.ASSERTION_LOOSENED]
    assert "tolerance was widened" in findings[0].message


def test_shadowed_not_implemented_error_is_clean() -> None:
    old = "def f(NotImplementedError):\n    return 1\n"
    new = old.replace("return 1", "raise NotImplementedError('domain error')")
    assert detect_shortcuts([("calc.py", old, new)]) == []


def test_unittest_class_rename_preserves_receiver_identity() -> None:
    old = (
        "import unittest\nclass TestCalc(unittest.TestCase):\n"
        "    def test_f(self):\n        self.assertEqual(f(), 3)\n"
    )
    new = old.replace("TestCalc", "TestMath")
    assert detect_shortcuts([("test_calc.py", old, new)]) == []


@pytest.mark.parametrize("prefix", ["", "from .calc import add as op\n"])
def test_move_preserves_distinct_receivers_and_relative_imports(prefix: str) -> None:
    body = "assert left.add(1, 2) == 3" if not prefix else "assert op(1, 2) == 3"
    old = f"{prefix}def test_add():\n    {body}\n"
    new = (
        old.replace("left.add", "right.add")
        if not prefix
        else old.replace("import add", "import sub")
    )
    findings = detect_shortcuts(
        [("tests/test_old.py", old, None), ("tests/test_new.py", None, new)]
    )
    assert [f.kind for f in findings] == [ShortcutKind.TEST_REMOVED]


def test_skip_in_local_parameter_case_is_detected() -> None:
    old = "import pytest\ndef test_f():\n    case = pytest.param(2)\n    assert case\n"
    new = old.replace("pytest.param(2)", "pytest.param(2, marks=pytest.mark.skip)")
    assert [f.kind for f in detect_shortcuts([("test_calc.py", old, new)])] == [
        ShortcutKind.SKIP_ADDED
    ]


def test_extraction_does_not_hide_a_changed_call() -> None:
    old = "def test_f():\n    assert f(1) == 3\n"
    new = "def test_f():\n    result = g(1)\n    assert result == 3\n"
    assert [f.kind for f in detect_shortcuts([("test_calc.py", old, new)])] == [
        ShortcutKind.ASSERTION_LOOSENED
    ]
