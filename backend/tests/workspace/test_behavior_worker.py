"""The standalone worker runs with a package, without importing the backend."""

import ast
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from oversight.workspace import run_process
from oversight.workspace.behavior_run import WORKER as METHOD_WORKER
from oversight.workspace.behavior_worker import _receiver_class

WORKER = Path(__file__).parents[2] / "src/oversight/workspace/behavior_worker.py"


async def invoke(tmp_path: Path, body: str, calls: list[str], timeout_s: float = 1.0):
    (tmp_path / "sample.py").write_text(body)
    request, report = tmp_path / "request.json", tmp_path / "report.json"
    request.write_text(json.dumps([{"id": "f", "module": "sample", "name": "f", "calls": calls}]))
    process = await run_process(
        [
            sys.executable,
            "-B",
            str(WORKER),
            str(tmp_path),
            str(request),
            str(report),
            str(timeout_s),
        ],
        cwd=tmp_path,
        timeout_s=30,
        env={"PYTHONHASHSEED": "0", "HOME": str(tmp_path)},
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout == process.stderr == ""
    return json.loads(report.read_text())["f"]


@pytest.mark.anyio
async def test_typed_values_are_deterministic_and_distinct(tmp_path):
    calls = [repr(((value,), {})) for value in (None, True, 1, 1.0, -0.0, "1", b"1", [1], (1,))]
    rows = await invoke(tmp_path, "def f(x): return x\n", calls)
    encoded = [json.dumps(row, sort_keys=True) for row in rows["results"]]
    assert len(set(encoded)) == len(calls)
    assert all(row["kind"] == "value" for row in rows["results"])
    rows = await invoke(
        tmp_path,
        "def f(): return {1: (float('nan'), -0.0), 'set': {3, 1}, 'frozen': frozenset({1})}\n",
        ["((), {})", "((), {})"],
    )
    assert rows["results"][0] == rows["results"][1]
    assert '"nan"' in json.dumps(rows) and '"-0x0.0p+0"' in json.dumps(rows)


@pytest.mark.anyio
async def test_exceptions_timeout_output_and_deepcopy(tmp_path):
    body = (
        "import os\n"
        "def f(x=[]):\n"
        "    print('noise'); os.write(2, b'noise')\n"
        "    if x == 'raise': raise ValueError('bad' * 1000)\n"
        "    if x == 'hang':\n"
        "        while True: pass\n"
        "    x.append(1)\n"
        "    return x\n"
    )
    result = await invoke(
        tmp_path, body, ["((), {})", "((), {})", "(('raise',), {})", "(('hang',), {})", "((), {})"]
    )
    rows = result["results"]
    assert rows[0] == rows[1] == rows[4]  # Mutable defaults are copied too.
    assert rows[2]["kind"] == "raised" and rows[2]["class"] == "builtins.ValueError"
    assert len(rows[2]["message"]) <= 512
    assert rows[3] == {"kind": "timed_out"}


@pytest.mark.anyio
async def test_encoding_caps_cycles_and_object_fields(tmp_path):
    body = (
        "class Box:\n    def __init__(self): self.payload = [1, b'x']\n"
        "def f(x):\n"
        "    if x == 0: return Box()\n"
        "    if x == 1: return 'z' * 1000000\n"
        "    if x == 2: return list(range(100000))\n"
        "    a = []; a.append(a); return a\n"
    )
    result = await invoke(tmp_path, body, [repr(((i,), {})) for i in range(4)])
    text = json.dumps(result)
    assert len(text) < 16_384
    assert "sample.Box" in text and "payload" in text
    assert "cut" in text and "cycle" in text


@pytest.mark.anyio
async def test_import_failure_is_not_a_function_exception(tmp_path):
    result = await invoke(tmp_path, "raise ImportError('missing side')\n", ["((), {})"])
    assert result["problem"] == "import failed"
    assert not result["results"]


@pytest.mark.anyio
async def test_import_timeout_has_no_function_results(tmp_path):
    # An endless import must outlive even the worker's maximum one-second alarm.
    result = await invoke(tmp_path, "while True: pass\ndef f(): return 1\n", ["((), {})"])
    assert result["problem"] == "import failed"
    assert result["results"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("literal", ["not a literal", "((), {})"])
async def test_literal_and_binding_setup_errors_are_unrepresentable(tmp_path, literal):
    result = await invoke(tmp_path, "def f(x): return x\n", [literal])
    assert result["results"][0]["kind"] == "unrepresentable"


@pytest.mark.anyio
async def test_deepcopy_setup_error_is_not_a_function_exception(tmp_path):
    body = (
        "class Uncopyable:\n"
        "    def __deepcopy__(self, memo): raise ValueError('setup')\n"
        "def f(x=Uncopyable()): raise ValueError('body')\n"
    )
    result = await invoke(tmp_path, body, ["((), {})"])
    assert result["results"][0]["kind"] == "unrepresentable"


def test_worker_is_stdlib_only():
    tree = ast.parse(WORKER.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] in sys.stdlib_module_names for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module and node.module.split(".")[0] in sys.stdlib_module_names


@pytest.mark.anyio
@pytest.mark.parametrize(
    "expression",
    [
        "'a' * 1000 + str(x)",
        "list(range(40)) + [x]",
        "{i: i for i in range(40)} | {'tail': x}",
        "[[[[[[[[x]]]]]]]]",
        "object()",
    ],
)
async def test_full_fingerprint_or_refusal_prevents_display_collisions(tmp_path, expression):
    body = "def f(x): return " + expression + "\n"
    result = await invoke(tmp_path, body, ["((0,), {})", "((1,), {})"])
    before, after = result["results"]
    if expression == "object()":
        assert before["kind"] == after["kind"] == "unrepresentable"
    else:
        assert before != after
        assert before["fingerprint"] != after["fingerprint"]


@pytest.mark.anyio
async def test_exception_full_message_fingerprint_survives_clipping(tmp_path):
    result = await invoke(
        tmp_path, "def f(x): raise ValueError('a' * 1000 + str(x))\n", ["((0,), {})", "((1,), {})"]
    )
    before, after = result["results"]
    assert before["message"] == after["message"]
    assert before != after


@pytest.mark.anyio
async def test_fingerprint_timeout_excludes_uncertain_result(tmp_path):
    # Leave time for the tiny import; the call cannot finish under any alarm budget.
    body = "def f():\n    while True: pass\n"
    result = await invoke(tmp_path, body, ["((), {})"], timeout_s=0.2)
    assert result["problem"] is None, f"Expected a successful import before the call: {result}"
    assert result["results"][0]["kind"] in ("timed_out", "unrepresentable")


@pytest.mark.anyio
async def test_object_state_outside_dictionary_is_not_a_complete_identity(tmp_path):
    body = (
        "class Box:\n"
        "    __slots__ = ('hidden', '__dict__')\n"
        "    def __init__(self, x): self.hidden = x\n"
        "def f(x): return Box(x)\n"
    )
    result = await invoke(tmp_path, body, ["((0,), {})", "((1,), {})"])
    assert all(row["kind"] == "unrepresentable" for row in result["results"])


async def invoke_method(
    tmp_path, source, *, module="sample", properties=(), calls=None, name="change", kind="method"
):
    (tmp_path / (module + ".py")).write_text(source)
    request, report = tmp_path / "method-request.json", tmp_path / "method-report.json"
    literal = repr(("((), {})", "((), {})"))
    request.write_text(
        json.dumps(
            [
                {
                    "id": "method",
                    "module": module,
                    "name": name,
                    "receiver_class": "Box",
                    "method_kind": kind,
                    "properties": properties,
                    "calls": calls or [literal],
                }
            ]
        )
    )
    process = await run_process(
        [sys.executable, "-B", str(METHOD_WORKER), str(tmp_path), str(request), str(report), "0.2"],
        cwd=tmp_path,
        timeout_s=10,
        env={"HOME": str(tmp_path)},
    )
    assert process.returncode == 0, process.stderr
    row = json.loads(report.read_text())["method"]
    assert row["problem"] is None
    return row["results"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "source,module",
    [
        ("from collections import Counter as Box\n", "sample"),
        ("class Box:\n    def change(self): return 1\n", "test_sample"),
        ("class Box:\n    def change(self): return 1\n", "conftest"),
        ("from random import Random as Box\n", "sample"),
    ],
)
async def test_worker_rechecks_project_class_allowlist(tmp_path, source, module):
    [row] = await invoke_method(tmp_path, source, module=module)
    assert row["kind"] == "unrepresentable"


def receiver_fixture(tmp_path, monkeypatch, source):
    module = ModuleType("sample")
    module.__file__ = str(tmp_path / "sample.py")
    exec(source, module.__dict__)
    monkeypatch.setitem(sys.modules, "sample", module)
    target = {
        "module": "sample",
        "receiver_class": "Box",
        "name": "change",
        "method_kind": "method",
    }
    return module, target


@pytest.mark.parametrize(
    "source,overrides,reason",
    [
        (
            "class Box:\n    def change(self): return 1\nBox.__module__ = 'other'\n",
            {},
            "receiver is not a project class",
        ),
        (
            "class Other:\n    def change(self): return 1\nBox = Other\n",
            {},
            "receiver is not a simple class",
        ),
        (
            "class Base: pass\nclass Box(Base):\n    def change(self): return 1\n",
            {},
            "receiver is not a simple class",
        ),
        (
            "def f(self, name): return 0\nclass Box:\n    __getattr__ = f\n"
            "    def change(self): return 1\n",
            {},
            "custom receiver hooks",
        ),
        (
            "class Box:\n    def change(self): return 1\n",
            {"method_kind": "setter"},
            "method descriptor mismatch",
        ),
        (
            "class Box:\n    value = 0\n    def __init__(self): self.value = 1\n"
            "    def change(self): return 1\n",
            {"properties": ["value"]},
            "property allowlist mismatch",
        ),
    ],
    ids=["module", "qualname", "bases", "body-hook", "descriptor", "property-allowlist"],
)
def test_receiver_refusals_are_independent(tmp_path, monkeypatch, source, overrides, reason):
    _, target = receiver_fixture(tmp_path, monkeypatch, source)
    with pytest.raises(ValueError, match=reason):
        _receiver_class({**target, **overrides}, tmp_path)


@pytest.mark.anyio
async def test_metaclass_is_refused_before_reading_class_attributes(tmp_path, monkeypatch):
    source = """reads = []
class Meta(type):
    def __getattribute__(cls, name):
        reads.append(name)
        return super().__getattribute__(name)
class Box(metaclass=Meta):
    def change(self): return 1
"""
    module, target = receiver_fixture(tmp_path, monkeypatch, source)
    with pytest.raises(ValueError, match="receiver metaclass"):
        _receiver_class(target, tmp_path)
    assert module.reads == []
    [row] = await invoke_method(tmp_path, source)
    assert row["kind"] == "unrepresentable"


@pytest.mark.anyio
async def test_setter_defaulted_extra_is_setup_failure_not_project_raise(tmp_path):
    source = """class Box:
    @property
    def value(self): return 0
    @value.setter
    def value(self, value: int): pass
"""
    calls = [repr(("((), {})", "((1,), {})"))]
    [base] = await invoke_method(tmp_path, source, name="value", kind="setter", calls=calls)
    [head] = await invoke_method(
        tmp_path,
        source.replace("value: int)", "value: int, extra=1)"),
        name="value",
        kind="setter",
        calls=calls,
    )
    assert base["kind"] == "value"
    assert head["kind"] == "unrepresentable"  # Never an artificial "Now raises" difference.


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["method", "property", "setter"])
async def test_method_argument_errors_are_not_project_exceptions(tmp_path, kind):
    source = "class Box:\n    def change(self, value): return value\n"
    if kind == "property":
        source = "class Box:\n    @property\n    def change(self, extra=1): return extra\n"
    elif kind == "setter":
        source = (
            "class Box:\n    @property\n    def change(self): return 0\n"
            "    @change.setter\n    def change(self, value): pass\n"
        )
    [row] = await invoke_method(tmp_path, source, kind=kind)
    assert row["kind"] == "unrepresentable"


@pytest.mark.anyio
async def test_method_construction_and_capture_share_the_call_alarm(tmp_path):
    source = (
        "class Box:\n    def __init__(self):\n        while True: pass\n"
        "    def change(self): return 1\n"
    )
    [row] = await invoke_method(tmp_path, source)
    assert row == {"kind": "timed_out"}


@pytest.mark.anyio
async def test_method_creates_fresh_receiver_per_input_and_tags_state_surfaces(tmp_path):
    source = """class Box:
    def __init__(self): self.value = 0
    def change(self): self.value += 1
    @property
    def doubled(self): return self.value * 2
"""
    literal = repr(("((), {})", "((), {})"))
    rows = await invoke_method(tmp_path, source, properties=["doubled"], calls=[literal, literal])
    assert rows[0] == rows[1]
    assert rows[0]["state"] == {
        "stored": [["value", ["int", "0x1"]]],
        "properties": [["doubled", ["int", "0x2"]]],
    }
    assert rows[0]["result_fingerprint"] != rows[0]["fingerprint"]


@pytest.mark.anyio
async def test_method_state_display_is_inside_the_result_size_cap(tmp_path):
    assignments = "; ".join(f"self.a{i} = 'x' * 512" for i in range(40))
    source = f"class Box:\n    def change(self): {assignments}\n"
    [row] = await invoke_method(tmp_path, source)
    assert row["kind"] == "unrepresentable" and len(json.dumps(row)) <= 8192
