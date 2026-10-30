"""One-shot pairs and atomic validation, including pin-compatible malformed input."""

from importlib import import_module

import pytest

OneToOne = import_module("boltons.dictutils").OneToOne


def test_one_shot_pairs():
    m = OneToOne()
    pairs = [("a", 1), ("b", 2)]
    try:
        m.update([iter(pair) for pair in pairs])
    except ValueError as error:
        pytest.fail(str(error))
    assert dict(m) == dict(pairs)
    assert dict(m.inv) == {1: "a", 2: "b"}


@pytest.mark.parametrize("bad", [(), ("x",), ("x", 2, 3)])
def test_malformed_pair_does_not_mutate(bad):
    m = OneToOne({"seed": 9})
    with pytest.raises(ValueError):
        m.update([("valid", 1), iter(bad)])
    assert dict(m) == {"seed": 9} and dict(m.inv) == {9: "seed"}
