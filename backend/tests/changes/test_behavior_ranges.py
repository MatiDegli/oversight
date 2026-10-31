"""Range inputs retain constructor triples, including unequal but sequence-equal ranges."""

import ast

import pytest

from oversight.changes import behavior_inputs
from oversight.changes._behavior_builtins import range_argument
from oversight.changes._behavior_objects import input_identity, invocation_values
from oversight.changes._shortcuts_common import Source


def prepare(calls):
    files = {"pkg/mod.py": "def f(src, *, fill): return tuple(src)\n", "test_mod.py": calls}
    return behavior_inputs("pkg.mod", "f", files, files)


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("range(5)", range(5)),
        ("range(-2, 5)", range(-2, 5)),
        ("range(0, 10, 2)", range(0, 10, 2)),
        ("range(5, 0, -1)", range(5, 0, -1)),
        ("range(10000)", range(10000)),
    ],
)
def test_range_is_data_and_keeps_literal_keywords(expression, expected):
    found = prepare(f"from pkg.mod import f\nf({expression}, fill=None)\n")
    assert found.reason is None
    assert found.inputs
    sample = found.inputs[0]
    assert sample.call == f"f(src={expected!r}, fill=None)"
    assert sample.input_identity is not None
    assert ast.literal_eval(sample.before)[2]["src"] == (
        "range",
        expected.start,
        expected.stop,
        expected.step,
    )
    _, kwargs = invocation_values(sample.before)
    assert type(kwargs["src"]) is range
    assert kwargs["fill"] is None


@pytest.mark.parametrize(
    "expression,reason",
    [
        ("range()", "range requires one to three int literals"),
        ("range(1, 2, 3, 4)", "range requires one to three int literals"),
        ("range(stop=5)", "range requires one to three int literals"),
        ("range(True)", "range requires one to three int literals"),
        ("range(5.0)", "range requires one to three int literals"),
        ("range(1 + 4)", "range requires one to three int literals"),
        ("range(*[5])", "range requires one to three int literals"),
        ("range(N)", "range requires one to three int literals"),
        ("range(0, 5, 0)", "range step is zero"),
        ("range(10001)", "range length cap"),
        ("range(10**100)", "range requires one to three int literals"),
        (f"range({10**100})", "range length cap"),
    ],
)
def test_range_refusal_has_fixed_reason(expression, reason):
    source = Source(expression)
    node = source.tree.body[0]
    assert isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    with pytest.raises(ValueError, match=reason):
        range_argument(source, node.value)
    assert not prepare(f"from pkg.mod import f\nf({expression}, fill=None)\n").inputs


@pytest.mark.parametrize(
    "scope",
    [
        "range = other\nf(range(5), fill=None)",
        "from elsewhere import range\nf(range(5), fill=None)",
        "from elsewhere import *\nf(range(5), fill=None)",
        "def test_it(range): f(range(5), fill=None)",
        "def test_it():\n    f(range(5), fill=None)\n    range = other",
        "def test_it():\n    global range\n    f(range(5), fill=None)",
        "[f(range(5), fill=None) for range in values]",
        "(lambda range: f(range(5), fill=None))(other)",
    ],
)
def test_shadowed_range_does_not_seed(scope):
    assert not prepare("from pkg.mod import f\n" + scope).inputs


def test_range_identity_uses_triple_instead_of_sequence_equality():
    a, b = range(0, 3, 2), range(0, 4, 2)
    assert a == b
    assert input_identity({"src": a}, {}, float("inf")) != input_identity(
        {"src": b}, {}, float("inf")
    )
    assert input_identity({"src": a}, {}, float("inf")) == input_identity(
        {"src": range(0, 3, 2)}, {}, float("inf")
    )
    assert input_identity({"src": a}, {}, float("inf")) != input_identity(
        {"src": ("range", 0, 3, 2)}, {}, float("inf")
    )
