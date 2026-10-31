"""Real-code signature shapes, using only AST inputs and temporary own fixtures."""

import ast

import pytest

from oversight.changes import behavior_inputs


def prepare(source, calls, *, path="tests/test_mod.py", after=None):
    base = {"pkg/mod.py": source, path: calls}
    head = {**base, "pkg/mod.py": after or source}
    return behavior_inputs("pkg.mod", "f", base, head)


@pytest.mark.parametrize("path", ["tests/test_mod.py", "pkg/caller.py"])
@pytest.mark.parametrize(
    "binding",
    [
        "import pkg.mod as mod\ncall = mod.f",
        "import pkg.mod\ncall = pkg.mod.f",
        "from pkg import mod\ncall = mod.f",
        "from pkg.mod import f\ncall = f",
        "from pkg.mod import f as imported\ncall = imported",
        "from pkg import mod\ncall: object = mod.f",
    ],
)
def test_module_alias_seeds_target(path, binding):
    found = prepare("def f(text): return text\n", binding + "\ncall('seed')\n", path=path)
    assert found.reason is None
    assert found.inputs[0].call == "f(text='seed')"


@pytest.mark.parametrize("path", ["pkg/test_mod.py", "pkg/__init__.py"])
def test_relative_import_alias_seeds_target(path):
    found = prepare(
        "def f(text): return text\n", "from .mod import f\ncall = f\ncall('seed')\n", path=path
    )
    assert found.reason is None
    assert found.inputs[0].call == "f(text='seed')"


@pytest.mark.parametrize(
    "binding",
    [
        "call = mod.f\ncall = other",
        "call = mod.f\ncall = mod.f",
        "call = mod.f\ncall += other",
        "call = mod.f\ndel call",
        "call = mod.f\nfrom elsewhere import call",
        "call = mod.f\ndef call(text): return text",
        "call = mod.f\nclass call: pass",
        "call = mod.f\nif flag: call = other",
        "call = mod.f\nfor call in values: pass",
        "call = mod.f\ntry: pass\nexcept Exception as call: pass",
        "call = mod.f\ndef reset():\n    global call\n    call = other",
        "call = mod.f\nmatch value:\n    case call: pass",
        "call = mod.f\nmatch value:\n    case {'key': _, **call}: pass",
        "call = mod.f\n[(call := other) for _ in values]",
        "call = mod.f\ndef test_it(value=(call := other)): pass",
        "call = mod.f\nother = lambda value=(call := other): value",
        "call = mod.f\nclass C((call := other)): pass",
        "call = mod.f\ndef other(value: (call := other)): pass",
        "call = mod.f\nfrom elsewhere import *",
        "call = mod.f\nmod = other",
        "call = mod.f\nimport pkg.mod as mod",
        "call = mod.f\ndef reset():\n    global mod\n    mod = other",
        "if flag: call = mod.f",
        "first = mod.f\ncall = first",
    ],
)
def test_ambiguous_alias_stays_unresolved(binding):
    found = prepare(
        "def f(text): return text\n", "from pkg import mod\n" + binding + "\ncall('seed')\n"
    )
    assert found.inputs == ()
    assert found.reason == "required argument has no literal value"


@pytest.mark.parametrize(
    "calls",
    [
        "def test_it(call): call('shadow')",
        "def test_it():\n    call('shadow')\n    call = other",
        "def outer(call):\n    def test_it(): call('shadow')",
        "class TestIt:\n    call = other\n    call('shadow')",
        "[call('shadow') for call in values]",
        "(call('shadow') for call in values)",
        "{call('shadow') for call in values}",
        "{call('shadow'): 0 for call in values}",
        "(lambda call: call('shadow'))(other)",
        "(lambda: (call('shadow'), (call := other)))()",
        "def test_it(value):\n    call('shadow')\n    match value:\n        case call: pass",
        "def test_it():\n    from elsewhere import *\n    call('shadow')",
    ],
)
def test_shadowed_alias_does_not_seed_target(calls):
    found = prepare(
        "def f(text): return text\n",
        "from pkg import mod\ncall = mod.f\ncall('seed')\n" + calls + "\n",
    )
    assert found.reason is None
    assert {ast.literal_eval(item.before)[1]["text"] for item in found.inputs} == {"seed"}


@pytest.mark.parametrize(
    "signature, call, explicit",
    [
        ("value=_UNSET", "f(value=None)", ((), {"value": None})),
        ("value=_UNSET, /", "f(None)", ((None,), {})),
        ("*, value=_UNSET", "f(value=None)", ((), {"value": None})),
        ("value=sentinel()", "f(value=None)", ((), {"value": None})),
        ("value=holder.sentinel", "f(value=None)", ((), {"value": None})),
        ("value=1 + 2", "f(value=None)", ((), {"value": None})),
    ],
)
def test_opaque_default_requires_explicit_supported_input(signature, call, explicit):
    found = prepare(f"def f({signature}): return value\n", f"from pkg.mod import f\nf()\n{call}\n")
    assert found.reason is None
    assert len(found.inputs) == 1
    assert found.skipped_binding == 2
    item = found.inputs[0]
    assert ast.literal_eval(item.before) == ast.literal_eval(item.after) == explicit
    assert item.input_identity is not None


@pytest.mark.parametrize("side", ["base", "head"])
def test_opaque_default_on_either_side_is_never_omitted(side):
    literal = "def f(value=5): return value\n"
    opaque = "def f(value=_UNSET): return value\n"
    before, after = (opaque, literal) if side == "base" else (literal, opaque)
    found = prepare(before, "from pkg.mod import f\nf()\n", after=after)
    assert found.reason is None
    assert found.skipped_binding == 2
    assert all("value=" in item.call and item.input_identity for item in found.inputs)
    assert all(
        ast.literal_eval(item.before) == ast.literal_eval(item.after) for item in found.inputs
    )


def test_typed_opaque_parameter_can_use_supported_integer_pool():
    found = prepare("def f(value: int = _UNSET): return value\n", "")
    assert found.reason is None
    assert {ast.literal_eval(item.before)[1]["value"] for item in found.inputs} == {0, -1}
    assert all(item.input_identity is not None for item in found.inputs)


@pytest.mark.parametrize("signature", ["value=_UNSET", "value=_UNSET, /", "*, value=_UNSET"])
def test_opaque_parameter_without_supported_value_produces_no_input(signature):
    found = prepare(f"def f({signature}): return value\n", "from pkg.mod import f\nf()\n")
    assert found.inputs == ()
    assert found.reason == "opaque default requires an explicit argument"


def test_literal_defaults_keep_omissions_and_effective_argument_identity():
    found = prepare(
        "def f(value=1): return value\n",
        "from pkg.mod import f\nf()\nf(value=3)\n",
        after="def f(value=2): return value\n",
    )
    omitted, explicit = found.inputs[:2]
    assert omitted.call == "f()"
    assert ast.literal_eval(omitted.before) == ast.literal_eval(omitted.after) == ((), {})
    assert omitted.input_identity is None
    assert explicit.call == "f(value=3)"
    assert explicit.input_identity is not None
    assert (
        prepare("def f(value=1): return value\n", "from pkg.mod import f\nf()\n")
        .inputs[0]
        .input_identity
        is not None
    )


@pytest.mark.parametrize("default", [repr("x" * 4097), repr(list(range(129)))])
def test_oversized_literal_default_still_rejects_signature(default):
    found = prepare(f"def f(value={default}): return value\n", "from pkg.mod import f\nf(1)\n")
    assert found.inputs == ()
    assert found.reason == "unsupported signature"


@pytest.mark.parametrize("name", ["windowed", "windowed_iter", "ellipsize"])
def test_boltons_shaped_temporary_project_now_has_inputs(tmp_path, name):
    # Signatures and alias spelling only from Boltons d31d821; bodies are our own.
    if name == "ellipsize":
        module = "boltons.strutils"
        source = "def ellipsize(text, max_len=160, *, ellipsis='…'):\n    return text\n"
        calls = (
            "from boltons import strutils\nellipsize = strutils.ellipsize\n"
            "ellipsize('Hello, World!', 10)\n"
        )
    else:
        module = "boltons.iterutils"
        source = f"_UNSET = object()\ndef {name}(src, size, fill=_UNSET):\n    return src\n"
        calls = f"from boltons.iterutils import {name}\n{name}([1, 2], 2, fill=None)\n"
    relative = module.replace(".", "/") + ".py"
    (tmp_path / "boltons").mkdir()
    (tmp_path / relative).write_text(source)
    (tmp_path / "test_calls.py").write_text(calls)
    files = {relative: (tmp_path / relative).read_text(), "test_calls.py": calls}
    found = behavior_inputs(module, name, files, files)
    assert found.reason is None
    assert found.inputs
    for item in found.inputs:
        assert item.input_identity is not None
        if name != "ellipsize":
            assert "fill=" in item.call
            assert ast.literal_eval(item.before)[1]["fill"] is None


def test_single_binding_local_alias_seeds_target():
    found = prepare(
        "def f(text): return text\n",
        "from pkg import mod\ndef test_it():\n    call = mod.f\n    call('local')\n",
    )
    assert [item.call for item in found.inputs] == ["f(text='local')"]
