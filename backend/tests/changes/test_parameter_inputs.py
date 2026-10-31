"""Only supplied proposed-test invocations enter the parameter-extension lane."""

import ast

import pytest

from oversight.changes import behavior_inputs
from oversight.changes._parameter_inputs import extension_inputs


def prepare(before="x", after="x, *, extra=0", call="f(2, extra=3)", path="test_sample.py"):
    base = {"sample.py": f"def f({before}): return x\n"}
    head = {
        "sample.py": f"def f({after}): return x\n",
        path: "from sample import f\n" + call + "\n",
    }
    return base, head, extension_inputs("sample", "f", base, head)


@pytest.mark.parametrize("before", ["x", "x, **kwargs", "x, **extra"])
def test_supplied_keyword_is_encoded_identically_for_both_versions(before):
    base, head, generated = prepare(before=before)
    assert generated is not None
    assert generated.parameters == ("extra",)
    [sample] = generated.samples
    assert ast.literal_eval(sample.before) == ((2,), {"extra": 3})
    assert sample.before == sample.after
    # The new lane cannot make new-parameter calls ordinary behavior comparisons.
    assert all(
        "extra=" not in item.call for item in behavior_inputs("sample", "f", base, head).inputs
    )


def test_new_positional_parameter_keeps_original_positional_invocation():
    _, _, generated = prepare(after="x, extra=0", call="f(2, 3)")
    assert generated and ast.literal_eval(generated.samples[0].before) == ((2, 3), {})


@pytest.mark.parametrize(
    "updates",
    [
        {"call": "f(2)"},
        {"call": "f(2, extra=3)", "path": "caller.py"},
        {"call": "f(2, extra=unknown)"},
        {"call": "f(extra=3)"},
        {"call": "f(2, **{'extra': 3})"},
        {"after": "x, extra=0", "before": "x, extra=0"},
    ],
)
def test_omitted_existing_non_test_and_unrepresentable_arguments_are_excluded(updates):
    assert prepare(**updates)[2] is None


def test_base_only_test_call_is_not_a_proposed_supplied_invocation():
    base, head, _ = prepare()
    base["test_old.py"] = head.pop("test_sample.py")
    assert extension_inputs("sample", "f", base, head) is None


def test_local_alias_and_range_data_are_reused():
    _, _, generated = prepare(call="def test_it():\n    target = f\n    target(range(3), extra=4)")
    assert generated is not None
    raw = ast.literal_eval(generated.samples[0].before)
    assert raw[1] == (("range", 0, 3, 1),)
    assert raw[2] == {"extra": ("literal", 4)}


def test_method_call_keeps_its_own_constructor_and_arguments():
    before = "class Box:\n    def __init__(self, x): self.x = x\n    def f(self): return self.x\n"
    after = before.replace("def f(self)", "def f(self, *, extra=0)")
    base = {"sample.py": before}
    head = {
        "sample.py": after,
        "test_sample.py": "from sample import Box\nBox(2).f(extra=3)\nBox(5).f(extra=6)\n",
    }
    generated = extension_inputs("sample", "Box.f", base, head, method=True)
    assert generated and generated.receiver_class == "Box"
    assert len(generated.samples) == 2
    for sample, expected in zip(generated.samples, [(2, 3), (5, 6)], strict=True):
        constructor, invocation = ast.literal_eval(sample.before)
        assert ast.literal_eval(constructor) == ((expected[0],), {})
        assert ast.literal_eval(invocation) == ((), {"extra": expected[1]})
        assert sample.before == sample.after


def test_parameters_list_only_names_passed_by_supplied_calls_and_inputs_stay_bounded():
    _, _, generated = prepare(
        after="x, extra=0, omitted=0", call="\n".join(f"f(1, extra={i})" for i in range(60))
    )
    assert generated and generated.parameters == ("extra",)
    assert len(generated.samples) == 48
    assert generated.reason == "budget exhausted"


@pytest.mark.parametrize("consumed", [False, True])
def test_generator_extension_requires_existing_consumption_shape(consumed):
    base = {"sample.py": "def f(): yield 1\n"}
    head = {
        "sample.py": "def f(extra=0): yield extra\n",
        "test_sample.py": "from sample import f\n"
        + ("list(f(extra=3))" if consumed else "f(extra=3)"),
    }
    generated = extension_inputs("sample", "f", base, head)
    assert bool(generated) == consumed
    if generated:
        assert "oversight.iterator_items.v1" in generated.samples[0].before
