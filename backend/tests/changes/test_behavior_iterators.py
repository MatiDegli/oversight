"""Only admitted list-consumed test invocations opt a target into iterator observation."""

from unittest.mock import AsyncMock

import pytest
from backend.tests.workspace.test_behavior_worker import invoke

from oversight.changes import behavior_inputs
from oversight.changes._behavior import BehaviorDiff
from oversight.changes._behavior_publish import (
    MAX_RENDERED,
    _render,
    capture_behavior,
    project_behavior,
)
from oversight.changes._review_identity import direct_result
from oversight.workspace import BehaviorObservation

from .test_behavior_identity import compare


def prepare(tests, *, source="def f(): return iter((1, 2))\n", path="test_sample.py"):
    files = {"sample.py": source, path: "from sample import f\n" + tests + "\n"}
    return behavior_inputs("sample", "f", files, files)


@pytest.mark.parametrize(
    "test",
    [
        "list(f())",
        "def test_it():\n    assert list(f()) == [1, 2]",
        "class TestIt:\n    def test_it(self):\n        assert list(f()) == [1, 2]",
        "def test_it():\n    call = f\n    list(call())",
    ],
)
def test_exact_unshadowed_list_wrapper_enables_target_observation(test):
    found = prepare(test)
    assert found.reason is None
    assert found.consume_iterator


@pytest.mark.parametrize(
    "test",
    [
        "f()",
        "tuple(f())",
        "list(iter(f()))",
        "list([f()])",
        "list(*[f()])",
        "list(f(), 3)",
        "list(f(), extra=1)",
        "other.list(f())",
        "consume = list\nconsume(f())",
        "list = other\nlist(f())",
        "from elsewhere import list\nlist(f())",
        "from elsewhere import *\nlist(f())",
        "def test_it(list): list(f())",
        "def test_it():\n    list(f())\n    list = other",
        "def test_it():\n    global list\n    list(f())",
        "def test_it():\n    nonlocal list\n    list(f())",
        "def test_it():\n    from elsewhere import *\n    list(f())",
        "[list(f()) for list in values]",
        "(lambda list: list(f()))(other)",
        '"""list(f())"""',
        "list(f(999))",  # The target call does not bind, so the wrapper proves nothing.
    ],
)
def test_other_shapes_and_shadowed_list_keep_existing_observation(test):
    assert not prepare(test).consume_iterator


def test_production_call_site_does_not_enable_consumption():
    assert not prepare("list(f())", path="caller.py").consume_iterator


@pytest.mark.parametrize("side", [0, 1])
def test_one_admitted_site_on_either_side_enables_same_mode_for_both(side):
    plain = {"sample.py": "def f(): return iter((1,))\n"}
    consumed = {**plain, "test_sample.py": "from sample import f\nlist(f())\n"}
    files = [plain, plain]
    files[side] = consumed
    assert behavior_inputs("sample", "f", *files).consume_iterator


def test_generator_is_admitted_only_with_consumed_call_site():
    source = "def f():\n    yield 1\n    raise ValueError('mid-stream')\n"
    assert prepare("list(f())", source=source).consume_iterator
    assert prepare("f()", source=source).reason == "async or generator"
    assert prepare("list(f())", source="async def f(): return 1\n").reason == "async or generator"


@pytest.mark.parametrize(
    "signature",
    ["*args", "**kwargs", "x=" + repr("x" * 4097)],
    ids=["varargs", "kwargs", "oversized-default"],
)
@pytest.mark.parametrize("side", [0, 1])
@pytest.mark.parametrize("call", ["f()", "list(f())"])
def test_generator_signature_parse_failure_keeps_unsupported_reason(signature, side, call):
    files = [
        {"sample.py": "def f(): yield 1\n", "test_sample.py": "from sample import f\n" + call},
        {"sample.py": "def f(): yield 1\n", "test_sample.py": "from sample import f\n" + call},
    ]
    files[side] = {**files[side], "sample.py": f"def f({signature}): yield 1\n"}
    found = behavior_inputs("sample", "f", *files)
    assert found.reason == "unsupported signature"
    assert not found.inputs
    assert not found.consume_iterator


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["finite", "infinite", "raises"])
async def test_small_trees_compare_only_complete_iterator_observations(tmp_path, case):
    before = "def f(src): return iter(src)\n"
    after = "def f(src): return iter(tuple(src) + (9,))\n"
    if case == "infinite":
        after = "from itertools import repeat\ndef f(src): return repeat(1)\n"
    elif case == "raises":
        after = "def f(src):\n    yield next(iter(src))\n    raise ValueError('mid-stream')\n"
    tests = "from sample import f\ndef test_it():\n    call = f\n    list(call(range(3)))\n"
    base, head = ({"sample.py": source, "test_sample.py": tests} for source in (before, after))
    generated = behavior_inputs("sample", "f", base, head)
    assert generated.consume_iterator and generated.reason is None
    assert len(generated.inputs) == 1

    async def run_behavior(tree, targets, **_):
        [target] = targets
        assert all("oversight.iterator_items.v1" in literal for literal in target.calls)
        root = tmp_path / tree
        root.mkdir(exist_ok=True)
        report = await invoke(root, before if tree == "base" else after, list(target.calls))
        return {"sample:f": BehaviorObservation(tuple(report["results"]), report["problem"])}

    worker = AsyncMock(side_effect=run_behavior)
    measured = await compare(generated, worker)
    assert measured.compared == measured.differing == int(case != "infinite")
    if case == "infinite":
        assert measured.rows == () and worker.await_count == 2
        return
    assert worker.await_count == 4
    [row] = measured.rows
    assert not row.unstable and row.repeat_complete and row.inputs_equal
    if case == "raises":
        assert row.after["kind"] == "raised"
        assert "value" not in row.after  # The yielded prefix is never an equal answer.
    evidence = capture_behavior(BehaviorDiff(1, (measured,)))
    public = project_behavior(evidence, revision=1)
    assert public is not None
    assert public[0].compared_count == public[0].differing_count == 1
    sample = public[0].rows[0]
    assert sample.before.startswith('items of iterator: {"kind":"value"')
    assert sample.after.startswith("items of iterator: ")
    assert sample.inputs == "f(src=range(0, 3))"
    assert "observation" not in sample.before


def test_consumed_result_identity_cannot_match_a_returned_list():
    result = {"kind": "value", "value": ["list", [["int", "0x1"]], 1, "complete"]}
    assert direct_result(result) != direct_result({**result, "observation": "iterator_items"})


def test_prefix_is_retained_inside_public_render_cap():
    result = {"kind": "value", "value": ["str", "x" * 10000], "observation": "iterator_items"}
    rendered = _render(result)
    assert rendered.startswith("items of iterator: ")
    assert len(rendered) <= MAX_RENDERED


def test_unconsumed_generator_keeps_existing_refusal_before_effect_checks():
    source = "def f():\n    print('effect')\n    yield 1\n"
    assert prepare("f()", source=source).reason == "async or generator"
    assert prepare("list(f())", source=source).reason == "print or input"
    assert prepare("list(f(999))", source=source).reason == "async or generator"
    assert prepare("f()", source="def f(*args): yield 1\n").reason == "unsupported signature"
