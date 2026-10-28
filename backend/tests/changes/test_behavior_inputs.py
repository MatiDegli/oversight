"""Deterministic finite inputs, with logical parameter names shared across sides."""

import ast

import pytest

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


@pytest.mark.parametrize("side", ["base", "head"])
@pytest.mark.parametrize(
    "imports, body, reason",
    [
        ("", "global state; state = x", "global or nonlocal assignment"),
        ("", "nonlocal state; state = x", "global or nonlocal assignment"),
        ("", "open('data').read()", "file access"),
        ("from pathlib import Path as P", "P('data').write_text('x')", "file access"),
        ("", "print(x)", "print or input"),
        ("", "input()", "print or input"),
        ("import random as rng", "rng.random()", "randomness"),
        ("from random import randint as draw", "draw(0, x)", "randomness"),
        ("from time import time as clock", "clock()", "time access"),
        ("import datetime", "datetime.datetime.now()", "time access"),
        ("import os as system", "system.environ['HOME']", "environment access"),
        ("from os import getenv as env", "env('HOME')", "environment access"),
        ("import socket as net", "net.socket()", "network access"),
        (
            "from urllib.request import urlopen as fetch",
            "fetch('https://example.test')",
            "network access",
        ),
    ],
)
def test_own_effects_on_either_side_are_not_probed(side, imports, body, reason):
    pure = "def f(x: int): return x\n"
    effect = f"{imports}\ndef f(x: int):\n    {body}\n    return x\n"
    result = generate(effect, pure) if side == "base" else generate(pure, effect)
    assert result.reason == reason
    assert result.inputs == ()


def test_nested_definition_effects_do_not_belong_to_own_body():
    result = generate("def f(x: int):\n    def nested(): print(x)\n    return x\n")
    assert result.reason is None and result.inputs


def test_module_integer_boundaries_from_both_versions_and_every_body():
    before = (
        "TIERS = ((50, 20), (10, 10))\nFLAG = True\n"
        "def other(): return -7\n"
        "class C:\n    def method(self): return 30\n"
        "def f(x: int, y: int, z: int = 0): return x + y + z\n"
    )
    after = before.replace("50, 20", "60, 20")
    result = generate(before, after, "from pkg.mod import f\nf(500, 600)\n")
    assert result == generate(before, after, "from pkg.mod import f\nf(500, 600)\n")
    arguments = [ast.literal_eval(item.before)[1] for item in result.inputs]
    # All parameters get the first module tier before the shared input cap is hit.
    for param in ("x", "y", "z"):
        assert {49, 50, 51} <= {kwargs.get(param) for kwargs in arguments}
    single = generate(
        before.replace(", y: int, z: int = 0", ""), after.replace(", y: int, z: int = 0", "")
    )
    values = {ast.literal_eval(item.before)[1]["x"] for item in single.inputs}
    assert {59, 60, 61, 19, 20, 21, 9, 10, 11, -8, -7, -6, 29, 30, 31} <= values
    assert all(type(value) is int for value in values)
