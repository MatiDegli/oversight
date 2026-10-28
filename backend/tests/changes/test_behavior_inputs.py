"""Deterministic finite inputs, with logical parameter names shared across sides."""

import ast

from oversight.changes import behavior_inputs


def generate(before: str, after: str | None = None, tests: str = ""):
    return behavior_inputs(
        "pkg.mod",
        "f",
        {"pkg/mod.py": before, "tests/test_mod.py": tests},
        {"pkg/mod.py": after or before, "tests/test_mod.py": tests},
    )


def test_literal_calls_keywords_boundaries_defaults_and_order():
    before = "def f(x: int, *, label: str = 'old'):\n    return x < 10 and label == 'yes'\n"
    after = before.replace("< 10", "< 12").replace("'old'", "'new'")
    tests = "from pkg.mod import f as call\ncall(5, label='ok')\ncall(x=7)\ncall(True)\ncall()\n"
    found = generate(before, after, tests)
    assert found == generate(before, after, tests)
    assert found.skipped_binding == 2  # Same non-binding call on either side.
    assert found.inputs[0].call == "f(x=5, label='ok')"
    assert found.inputs[1].call == "f(x=7)"
    literals = [ast.literal_eval(item.before) for item in found.inputs]
    xs = [kwargs["x"] for args, kwargs in literals]
    assert {9, 10, 11, 12, 13, 0, -1} <= set(xs)
    assert all(type(x) is int for x in xs)
    labels = {kwargs.get("label") for args, kwargs in literals}
    assert {"old", "new", "yes", "ok"} <= labels


def test_cap_and_one_parameter_at_a_time():
    body = "def f(x: int, y: int):\n    return [" + ",".join(map(str, range(100))) + "]\n"
    found = generate(body, tests="from pkg.mod import f\nf(500, 600)\n")
    assert len(found.inputs) == 48
    assert found.inputs[0].call == "f(x=500, y=600)"
    assert all(
        kwargs["x"] == 500 or kwargs["y"] == 600
        for args, kwargs in (ast.literal_eval(item.before) for item in found.inputs)
    )


def test_name_binding_survives_parameter_order_and_positional_only():
    before = "def f(x: int, /, y: int = 2): return x + y\n"
    after = "def f(y: int = 2, *, x: int): return x + y\n"
    found = generate(before, after, "from pkg.mod import f\nf(3, y=4)\n")
    first = found.inputs[0]
    assert ast.literal_eval(first.before) == ((3,), {"y": 4})
    assert ast.literal_eval(first.after) == ((), {"y": 4, "x": 3})


def test_unsupported_async_generators_and_nonliteral_defaults():
    for body, reason in (
        ("async def f(x): return x", "async or generator"),
        ("def f(x): yield x", "async or generator"),
        ("def f(*args): return args", "unsupported signature"),
        ("def f(x=object()): return x", "unsupported signature"),
    ):
        assert generate(body).reason == reason
    assert generate("def f(x: int):\n    def nested(): yield 1\n    return x").reason is None


def test_literal_only_no_execution_and_shadowed_calls():
    body = "def f(x: int): return x\n"
    tests = "from pkg.mod import f\nf(17)\nf(1 + 2)\nf(*[3])\ndef test_shadow(f): f(999)\n"
    found = generate(body, tests=tests)
    values = [ast.literal_eval(item.before)[1]["x"] for item in found.inputs]
    assert 17 in values and 999 not in values and 3 not in values


def test_negative_constants_and_mutable_literal_types():
    found = generate("def f(x: int): return x < -7\n")
    values = {ast.literal_eval(item.before)[1]["x"] for item in found.inputs}
    assert {-8, -7, -6, 0, -1} <= values
    found = generate("def f(x = {'key': [1, 2]}): return x\n")
    assert ast.literal_eval(found.inputs[0].before) == ((), {"x": {"key": [1, 2]}})
