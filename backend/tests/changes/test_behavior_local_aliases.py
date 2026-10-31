"""Test-local aliases admit a unique direct assignment, never inferred flow."""

import pytest

from oversight.changes import behavior_inputs


def prepare(body, *, imports="from pkg import mod", path="tests/test_mod.py", args=""):
    tests = imports + f"\ndef test_it({args}):\n" + "\n".join("    " + s for s in body.splitlines())
    files = {"pkg/mod.py": "def f(text): return text\n", path: tests}
    return behavior_inputs("pkg.mod", "f", files, files)


@pytest.mark.parametrize(
    "imports,assignment",
    [
        ("from pkg import mod", "call = mod.f"),
        ("import pkg.mod as mod", "call = mod.f"),
        ("import pkg.mod", "call = pkg.mod.f"),
        ("from pkg.mod import f", "call = f"),
        ("from pkg.mod import f as imported", "call: object = imported"),
        ("from .mod import f", "call = f"),
    ],
)
def test_direct_local_alias(imports, assignment):
    found = prepare(assignment + "\ncall('seed')", imports=imports, path="pkg/test_mod.py")
    assert found.reason is None
    assert [item.call for item in found.inputs] == ["f(text='seed')"]


@pytest.mark.parametrize(
    "body",
    [
        "call('seed')\ncall = mod.f",
        "call = other()\ncall('seed')",
        "call = other = mod.f\ncall('seed')",
        "call = mod.f\ncall = mod.f\ncall('seed')",
        "call = mod.f\ncall += other\ncall('seed')",
        "call = mod.f\ndel call\ncall('seed')",
        "call = mod.f\nif flag: call = other\ncall('seed')",
        "if flag: call = mod.f\ncall('seed')",
        "for _ in values: call = mod.f\ncall('seed')",
        "with context(): call = mod.f\ncall('seed')",
        "try: call = mod.f\nexcept Exception: pass\ncall('seed')",
        "call = mod.f\nfor call in values: pass\ncall('seed')",
        "call = mod.f\ntry: pass\nexcept Exception as call: pass\ncall('seed')",
        "call = mod.f\nmatch value:\n    case call: pass\ncall('seed')",
        "call = mod.f\n[(call := other) for _ in values]\ncall('seed')",
        "call = mod.f\ndef call(): pass\ncall('seed')",
        "call = mod.f\nclass call: pass\ncall('seed')",
        "call = mod.f\nfrom elsewhere import call\ncall('seed')",
        "global call\ncall = mod.f\ncall('seed')",
        "nonlocal call\ncall = mod.f\ncall('seed')",
        "call = mod.f\nmod = other\ncall('seed')",
        "from pkg import mod\ncall = mod.f\ncall('seed')",
        "from elsewhere import *\ncall = mod.f\ncall('seed')",
        "call = mod.f\n[call('seed') for call in values]",
        "call = mod.f\n(lambda call: call('seed'))(other)",
        "call = mod.f\ndef nested(): call('seed')",
        "first = mod.f\ncall = first\ncall('seed')",
        'call = mod.f\n"""call(\'seed\')"""',
    ],
)
def test_ambiguous_local_alias_is_refused(body):
    found = prepare(body)
    assert not found.inputs
    assert found.reason == "required argument has no literal value"


@pytest.mark.parametrize("args", ["call", "mod"])
def test_parameters_hide_alias_or_import(args):
    assert not prepare("call = mod.f\ncall('seed')", args=args).inputs


@pytest.mark.parametrize(
    "imports",
    [
        "from pkg import mod\nfrom elsewhere import *",
        "from pkg import mod\nfrom pkg import mod",
        "from pkg import mod\nmod = other",
        "from pkg import mod\ndef reset():\n    global mod\n    mod = other",
        "from pkg import mod\ndef reset():\n    global call\n    call = other",
    ],
)
def test_module_import_must_remain_unambiguous(imports):
    assert not prepare("call = mod.f\ncall('seed')", imports=imports).inputs


def test_non_test_file_and_non_test_function_stay_out_of_scope():
    assert not prepare("call = mod.f\ncall('seed')", path="pkg/caller.py").inputs
    files = {
        "pkg/mod.py": "def f(text): return text\n",
        "test_mod.py": "from pkg import mod\ndef helper():\n    call = mod.f\n    call('seed')\n",
    }
    assert not behavior_inputs("pkg.mod", "f", files, files).inputs
