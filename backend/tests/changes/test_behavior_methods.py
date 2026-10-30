"""Finite method observations use literal receivers and compare public state."""

import ast
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from oversight.changes._behavior import BehaviorDiff, _compare, _prepare, targets
from oversight.changes._behavior_inputs import BehaviorInput, BehaviorInputs
from oversight.changes._behavior_publish import capture_behavior, project_behavior
from oversight.workspace import BehaviorObservation
from oversight.workspace.behavior_run import WORKER
from oversight.workspace.runner import run_process

PLAIN = """class Box:
    def __init__(self, value=1): self.value = value
    def change(self, amount: int = 0):
        self.value += amount
        return None
"""
CALLS = "from sample import Box\nBox(2)\n"


def prepared(
    before=PLAIN,
    after=None,
    *,
    qualified="Box.change",
    calls=CALLS,
    path="sample.py",
    call_path="tests/test_sample.py",
):
    module = path.removesuffix(".py").replace("/", ".")
    node = SimpleNamespace(
        id=f"{module}:{qualified}",
        path=path,
        name=qualified.split(".")[-1].split("#")[0],
        kind="method",
        parent_id=f"{module}:Box",
        line=999,
    )
    cls = SimpleNamespace(id=f"{module}:Box", kind="class", parent_id=module)
    parent = SimpleNamespace(id=module, kind="module")
    version = SimpleNamespace(graph=SimpleNamespace(nodes=[node, cls, parent]))
    base = {call_path: calls, path: before}
    rec = SimpleNamespace(
        base=version,
        head=version,
        base_files=base,
        head_files={**base, path: before if after is None else after},
        graph=SimpleNamespace(nodes_added=[], nodes_removed=[], nodes_modified=[node.id]),
    )
    found = list(targets(cast(Any, rec)))
    generated = _prepare(cast(Any, rec), found, time.monotonic() + 10)
    return rec, generated, found


async def worker(root: Path, requests):
    request, report = root / "request.json", root / "report.json"
    request.write_text(json.dumps([asdict(item) for item in requests]))
    process = await run_process(
        [sys.executable, "-B", str(WORKER), str(root), str(request), str(report), "0.2"],
        cwd=root,
        timeout_s=20,
        env={"PYTHONHASHSEED": "0", "HOME": str(root)},
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout == process.stderr == ""
    return {
        key: BehaviorObservation(tuple(row["results"]), row["problem"])
        for key, row in json.loads(report.read_text()).items()
    }


async def measured(tmp_path, before=PLAIN, after=None, **kwargs):
    rec, generated, found = prepared(before, after, **kwargs)
    assert generated, found[0].reason  # Also gives a behavioral assertion on the old pipeline.
    roots = [tmp_path / "base", tmp_path / "head"]
    for root, files in zip(roots, (rec.base_files, rec.head_files), strict=True):
        root.mkdir()
        for path, source in files.items():
            file = root / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(source)

    async def run(root, requests, *, timeout_s):
        return await worker(root, requests)

    await _compare(
        cast(Any, SimpleNamespace(run_behavior=run)),
        cast(Any, rec),
        cast(Any, roots),
        generated,
        found,
        time.monotonic() + 60,
        set(),
    )
    return found[0], generated


@pytest.mark.anyio
async def test_state_only_change_is_measured_with_constructor_and_bounded_public_state(tmp_path):
    result, _generated = await measured(tmp_path, after=PLAIN.replace("+= amount", "+= amount + 1"))
    assert result.compared == result.differing > 0
    assert len(result.rows) <= 8
    assert result.groups and len(result.groups) == 1
    assert result.groups is not None
    assert result.groups[0].kind.description == "Leaves the object in a different state"
    public = project_behavior(capture_behavior(BehaviorDiff(1, (result,))), revision=1)
    assert public and public[0].rows[0].inputs.startswith("Box(2).change(")
    row = public[0].rows[0]
    assert json.loads(row.before)["state"]["stored"] == [["value", ["int", "0x2"]]]
    assert not any("fingerprint" in text for text in (row.before, row.after))
    assert all(len(row.inputs) <= 1024 for row in public[0].rows)


@pytest.mark.anyio
async def test_setter_ordinal_and_shifted_lines_resolve_each_ast_independently(tmp_path):
    before = """class Box:
    def __init__(self, value=1): self._value = value
    @property
    def value(self): return self._value
    @value.setter
    def value(self, value: int): self._value = value
"""
    after = "\n\n\n" + before.replace("self._value = value\n", "self._value = value\n", 1)
    after = after.rsplit("self._value = value", 1)[0] + "self._value = value + 1\n"
    result, _ = await measured(tmp_path, before, after, qualified="Box.value#2")
    assert result.node_id == "sample:Box.value#2" and result.differing > 0
    assert all(row.call.startswith("Box(2).value = ") for row in result.rows)
    assert all(row.before["value"] == ["none"] for row in result.rows)
    assert result.groups is not None
    assert result.groups[0].kind.description == "Leaves the object in a different state"
    assert result.rows[0].after["state"]["properties"][0][0] == "value"


@pytest.mark.parametrize(
    "decorator,reason",
    [
        ("@classmethod", "classmethod"),
        ("@staticmethod", "staticmethod"),
        ("@change.deleter", "property deleter"),
        ("@custom", "other decorator"),
    ],
)
def test_decorator_exclusions_have_fixed_reasons(decorator, reason):
    source = "class Box:\n    " + decorator + "\n    def change(self): return 1\n"
    _, generated, found = prepared(source)
    assert not generated and found[0].reason == reason


@pytest.mark.parametrize(
    "source,qualified,reason",
    [
        (
            "class Box(Base):\n    def change(self): pass\n",
            "Box.change",
            "class bases or metaclass",
        ),
        (
            "class Box(metaclass=Meta):\n    def change(self): pass\n",
            "Box.change",
            "class bases or metaclass",
        ),
        ("@decorate\nclass Box:\n    def change(self): pass\n", "Box.change", "class decorator"),
        ("class Box:\n    async def change(self): pass\n", "Box.change", "async method"),
        ("class Box:\n    def change(self): yield 1\n", "Box.change", "generator method"),
        ("class Box:\n    def __call__(self): pass\n", "Box.__call__", "dunder method"),
        (
            "class Outer:\n    class Box:\n        def change(self): pass\n",
            "Outer.Box.change",
            "nested class",
        ),
    ],
)
def test_class_and_method_exclusions_have_fixed_reasons(source, qualified, reason):
    _, generated, found = prepared(source, qualified=qualified)
    assert not generated and found[0].reason == reason


@pytest.mark.parametrize(
    "hook",
    [
        "__getattr__",
        "__getattribute__",
        "__setattr__",
        "__delattr__",
        "__slots__",
        "__del__",
        "__new__",
    ],
)
def test_custom_hooks_are_excluded(hook):
    declaration = (
        "    __slots__ = ()\n" if hook == "__slots__" else f"    def {hook}(self, *args): pass\n"
    )
    _, generated, found = prepared(PLAIN + declaration)
    assert not generated and found[0].reason == "custom object hooks"


@pytest.mark.parametrize("place", ["method", "constructor", "setter", "getter"])
@pytest.mark.parametrize("side", ["base", "head"])
def test_effect_filters_apply_on_both_sides_to_every_executed_body(place, side):
    clean = PLAIN + "    @property\n    def extra(self): return 1\n"
    dirty = clean
    if place == "method":
        dirty = dirty.replace("return None", "return random.random()")
    elif place == "constructor":
        dirty = dirty.replace("self.value = value", "self.value = random.random()")
    elif place == "setter":
        dirty += "    @extra.setter\n    def extra(self, value): self.value = random.random()\n"
    else:
        # A getter must be AST-identical to be in the shared projection.
        clean = clean.replace("return 1", "return random.random()")
        dirty = clean
    clean, dirty = "import random\n" + clean, "import random\n" + dirty
    _, generated, found = prepared(
        dirty if side == "base" else clean, dirty if side == "head" else clean
    )
    assert not generated
    assert found[0].reason == ("property getter effects" if place == "getter" else "randomness")


def test_changed_decorator_kind_is_not_resolved_using_head_lines():
    _, generated, found = prepared(
        PLAIN, PLAIN.replace("    def change", "    @property\n    def change")
    )
    assert not generated and found[0].reason == "method target mismatch"


@pytest.mark.parametrize(
    "calls",
    [
        "from sample import Box\nclass Child(Box): pass\nChild(2)\n",
        "from elsewhere import Box\nBox(2)\n",
        "from sample import Box\nBox = Other\nBox(2)\n",
        "from sample import Box\ndef test_it(Box): Box(2)\n",
        "from sample import Box\nBox(dynamic)\n",
    ],
)
def test_unresolved_subclass_and_nonliteral_receivers_are_not_used(calls):
    _, generated, found = prepared(calls=calls)
    assert not generated and found[0].reason == "no literal constructor receivers"


@pytest.mark.parametrize("call_path", ["tests/test_sample.py", "conftest.py", "sample.py"])
def test_constructor_aliases_and_four_receiver_cap(call_path):
    calls = "from sample import Box as Item\nCreate = Item\n" + "\n".join(
        f"Create({i})" for i in range(8)
    )
    source = PLAIN + calls if call_path == "sample.py" else PLAIN
    _, generated, found = prepared(source, calls=calls, call_path=call_path)
    assert generated, found[0].reason
    samples = next(iter(generated.values())).inputs
    receivers = {ast.literal_eval(item.before)[0] for item in samples}
    assert len(receivers) == 4 and len(samples) <= 48


@pytest.mark.anyio
async def test_private_post_state_and_head_only_property_do_not_create_differences(tmp_path):
    before = PLAIN.replace("self.value += amount", "self._private = amount")
    after = before.replace("self._private = amount", "self._private = amount + 1")
    after += "    @property\n    def extra(self): raise RuntimeError('head only')\n"
    result, _ = await measured(tmp_path, before, after)
    assert result.compared > 0 and result.differing == 0 and result.reason is None


@pytest.mark.anyio
async def test_initial_public_state_mismatch_is_not_a_method_difference(tmp_path):
    after = PLAIN.replace("self.value = value", "self.value = value + 1").replace(
        "return None", "return 9"
    )
    result, _ = await measured(tmp_path, PLAIN, after)
    assert result.compared == result.differing == 0 and result.rows == ()
    assert "unequal receiver state" in (result.reason or "")


@pytest.mark.anyio
async def test_hidden_uuid_constructor_helper_never_certifies_a_stable_input(tmp_path):
    before = "import uuid\ndef nonce(): return str(uuid.uuid4())\n" + PLAIN.replace(
        "self.value = value", "self.value = value; self._nonce = nonce()"
    )
    result, _ = await measured(tmp_path, before, before.replace("return None", "return 9"))
    assert result.compared == result.differing == 0 and result.rows == ()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "state",
    [
        "self.value = object()",
        "self.value = {1, 2}",
        "getter",
    ],
)
async def test_nonliteral_state_and_raising_getters_are_unrepresentable(tmp_path, state):
    before = (
        PLAIN + "    @property\n    def extra(self): raise ValueError('getter')\n"
        if state == "getter"
        else PLAIN.replace("self.value += amount", state)
    )
    result, _ = await measured(tmp_path, before, before.replace("return None", "return 9"))
    assert result.compared == result.differing == 0 and result.rows == ()
    assert "unrepresentable" in (result.reason or "")


@pytest.mark.anyio
async def test_public_state_is_captured_after_exception(tmp_path):
    before = PLAIN.replace("return None", "raise ValueError('same')")
    result, _ = await measured(tmp_path, before, before.replace("+= amount", "+= amount + 1"))
    assert result.differing > 0 and result.rows[0].before["kind"] == "raised"
    assert result.groups is not None
    assert result.groups[0].kind.description == "Leaves the object in a different state"


@pytest.mark.anyio
async def test_property_getter_is_invoked_and_return_difference_keeps_value_kind(tmp_path):
    before = (
        "class Box:\n    def __init__(self, value): self._value = value\n"
        "    @property\n    def change(self): return self._value\n"
    )
    result, _ = await measured(
        tmp_path, before, before.replace("return self._value", "return self._value + 1")
    )
    assert result.differing == 1
    assert result.rows[0].call == "Box(2).change"
    assert result.groups is not None
    assert result.groups[0].kind.description == "Returns a different value"


@pytest.mark.parametrize(
    "decorator,field",
    [
        ("dataclass(init=False)", "value: int"),
        ("dataclass(kw_only=True)", "value: int"),
        ("dataclass(slots=True)", "value: int"),
        ("dataclass", "value: int = field(default=1)"),
        ("dataclass", "value: InitVar[int]"),
        ("dataclass", "value: ClassVar[int]"),
        ("dataclass", "_: KW_ONLY"),
    ],
)
def test_unsupported_dataclass_constructors_are_excluded(decorator, field):
    source = (
        "from dataclasses import dataclass, field, InitVar, KW_ONLY\n"
        f"from typing import ClassVar\n@{decorator}\nclass Box:\n    {field}\n"
        "    def change(self): return 1\n"
    )
    _, generated, found = prepared(source)
    assert not generated and found[0].reason in {
        "class decorator",
        "unsupported dataclass constructor",
    }


@pytest.mark.anyio
async def test_frozen_dataclass_setter_uses_setattr_and_raises(tmp_path):
    before = """from dataclasses import dataclass
@dataclass(frozen=True, eq=True, order=True)
class Box:
    value: int
    @property
    def change(self): return self.value
    @change.setter
    def change(self, value: int): object.__setattr__(self, 'value', value)
"""
    after = before.replace("object.__setattr__(self, 'value', value)", "return None")
    result, _ = await measured(tmp_path, before, after, qualified="Box.change#2")
    assert result.compared > 0 and result.differing == 0
    # Direct worker assertion exposes the exception even for equal results (no public diff rows).
    rec, generated, _ = prepared(before, after, qualified="Box.change#2")
    from oversight.changes._behavior import _requests

    rows = await worker(tmp_path / "base", _requests(cast(Any, rec), generated, 0))
    assert all(
        row["kind"] == "raised" and row["class"] == "dataclasses.FrozenInstanceError"
        for observation in rows.values()
        for row in observation.results
    )


@pytest.mark.anyio
async def test_class_state_leaks_across_first_pass_are_unstable_on_fresh_repeats(tmp_path):
    before = PLAIN.replace("def change", "count = 0\n    def change").replace(
        "return None", "type(self).count += 1\n        return type(self).count"
    )
    after = before.replace("return type(self).count", "return type(self).count + amount")
    result, _ = await measured(tmp_path, before, after)
    assert result.differing <= 1 and any(row.unstable for row in result.rows)
    assert result.groups is not None
    assert all(group.count == 1 for group in result.groups)


@pytest.mark.parametrize("path", ["tests/test_sample.py", "test_sample.py", "conftest.py"])
def test_parent_refuses_classes_defined_in_test_modules(path):
    _, generated, found = prepared(path=path, call_path="tests/test_caller.py")
    assert not generated and found[0].reason == "test module"


def test_missing_project_class_definition_cannot_probe_stdlib():
    _, generated, found = prepared("from collections import Counter as Box\n")
    assert not generated and found[0].reason == "method target mismatch"


def test_direct_literal_method_calls_seed_unannotated_arguments():
    source = PLAIN.replace("amount: int = 0", "amount")
    calls = "from sample import Box as Item\nItem(2).change(7)\n"
    _, generated, found = prepared(source, calls=calls)
    assert generated, found[0].reason
    assert "amount=7" in next(iter(generated.values())).inputs[0].call


def test_dataclass_post_init_is_not_a_supported_constructor():
    source = (
        "from dataclasses import dataclass\n@dataclass\nclass Box:\n"
        "    value: int\n    def __post_init__(self): self.value += 1\n"
        "    def change(self): return self.value\n"
    )
    _, generated, found = prepared(source)
    assert not generated and found[0].reason == "unsupported dataclass constructor"


@pytest.mark.parametrize(
    "imports,annotation",
    [
        ("from typing import ClassVar as CV", "CV[int]"),
        ("from dataclasses import InitVar as IV", "IV[int]"),
        ("from dataclasses import KW_ONLY as KW", "KW"),
        ("import typing as t", "t.ClassVar[int]"),
        ("from typing import ClassVar as CV", "'CV[int]'"),
        ("from dataclasses import InitVar as IV", "'IV[int]'"),
        ("", "'Box'"),
        ("", "UnknownType"),
    ],
)
def test_dataclass_annotations_require_resolved_nonfield_semantics(imports, annotation):
    source = (
        f"from dataclasses import dataclass\n{imports}\n@dataclass\nclass Box:\n"
        f"    value: {annotation}\n    def change(self): return 1\n"
    )
    _, generated, found = prepared(source)
    assert not generated and found[0].reason == "unsupported dataclass constructor"


@pytest.mark.anyio
async def test_private_initialization_changes_are_unequal_inputs_not_method_output(tmp_path):
    before = PLAIN.replace("self.value = value", "self.value = value; self._secret = 1")
    after = before.replace("self._secret = 1", "self._secret = 2").replace(
        "return None", "return 9"
    )
    result, _ = await measured(tmp_path, before, after)
    assert result.compared == result.differing == 0 and result.rows == ()


@pytest.mark.anyio
async def test_state_only_differences_preserve_counts_beyond_eight_public_rows(tmp_path):
    calls = CALLS + "\n".join(f"Box(2).change({i})" for i in range(26))
    result, _ = await measured(
        tmp_path, after=PLAIN.replace("+= amount", "+= amount + 1"), calls=calls
    )
    assert result.compared == result.differing > 8 and len(result.rows) == 8
    assert result.groups and result.groups[0].count == result.differing


@pytest.mark.anyio
async def test_long_constructor_input_is_compared_and_only_public_display_is_clipped(tmp_path):
    before = (
        "class Box:\n    def __init__(self, value): self.value = value\n"
        "    def change(self): return 0\n"
    )
    calls = "from sample import Box\nBox(" + repr("x" * 1500) + ")\n"
    result, generated = await measured(
        tmp_path, before, before.replace("return 0", "return 1"), calls=calls
    )
    assert result.compared == result.differing == 1
    assert len(next(iter(generated.values())).inputs[0].call) > 1024
    public = project_behavior(capture_behavior(BehaviorDiff(1, (result,))), revision=1)
    assert public and len(public[0].rows[0].inputs) <= 1024


def test_invalid_unrelated_python_file_does_not_hide_literal_method_inputs():
    rec, _, found = prepared()
    assert found[0].reason is None
    rec.base_files["broken.py"] = "def broken(:"
    rec.head_files["broken.py"] = "def broken(:"
    generated = _prepare(cast(Any, rec), found, time.monotonic() + 10)
    assert generated and found[0].reason is None


@pytest.mark.parametrize("kind", ["property", "setter", "captured-property"])
@pytest.mark.parametrize("side", ["base", "head"])
def test_property_extra_parameters_are_refused_without_comparisons(kind, side):
    source = """class Box:
    def __init__(self, value=1): self._value = value
    @property
    def value(self): return self._value
    @value.setter
    def value(self, value: int): self._value = value
    def change(self): return 0
"""
    dirty = source.replace(
        "def value(self, value: int)" if kind == "setter" else "def value(self)",
        "def value(self, value: int, extra=1)" if kind == "setter" else "def value(self, extra=1)",
    )
    if kind == "captured-property":
        source = dirty  # Captured getters must be identical across sides.
    _, generated, found = prepared(
        dirty if side == "base" else source,
        dirty if side == "head" else source,
        qualified="Box.change"
        if kind == "captured-property"
        else "Box.value#2"
        if kind == "setter"
        else "Box.value",
    )
    assert not generated
    assert found[0].reason == (
        "unsupported setter signature" if kind == "setter" else "unsupported getter signature"
    )
    assert found[0].compared == found[0].differing == 0


@pytest.mark.anyio
async def test_tight_deadline_finishes_functions_before_method_work(monkeypatch):
    from oversight.changes import _behavior

    rec, inputs, found = prepared()
    method_id = found[0].node_id
    inputs[method_id] = BehaviorInputs(
        inputs[method_id].inputs[:1], receiver_class="Box", method_kind="method"
    )
    function = SimpleNamespace(id="sample:f", path="sample.py", name="f")
    rec.head.graph.nodes.append(function)
    inputs[function.id] = BehaviorInputs((BehaviorInput("f()", "((), {})", "((), {})", "same"),))
    found.append(_behavior.FunctionBehavior(function.id))  # Method sorts before function.
    clock = [0.0]
    monkeypatch.setattr(_behavior, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def run(tree, requests, *, timeout_s):
        if timeout_s < 0.2:
            raise TimeoutError
        clock[0] += 0.2
        return {
            target.id: BehaviorObservation(
                tuple(
                    {
                        "kind": "value",
                        "value": ["int", hex(tree)],
                        "fingerprint": str(tree),
                        "pre_fingerprint": "same",
                    }
                    for _ in target.calls
                )
            )
            for target in requests
        }

    completed = set()
    with pytest.raises(TimeoutError):
        await _compare(
            cast(Any, SimpleNamespace(run_behavior=run)),
            cast(Any, rec),
            cast(Any, [0, 1]),
            inputs,
            found,
            0.9,
            completed,
        )
    assert completed == {function.id}
    assert found[1].compared == found[1].differing == 1
    assert found[1].reason is None
