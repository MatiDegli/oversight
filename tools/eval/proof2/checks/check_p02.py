"""Optional update input and inverse compatibility observed on d31d821."""

from importlib import import_module

import pytest

OneToOne = import_module("boltons.dictutils").OneToOne


def test_no_argument_update():
    o = OneToOne({"a": 1})
    assert o.update() is None
    assert dict(o) == {"a": 1} and dict(o.inv) == {1: "a"}


def test_keyword_update():
    o = OneToOne({"a": 1})
    assert o.update(b=2) is None
    assert dict(o) == {"a": 1, "b": 2} and o.inv[2] == "b"


@pytest.mark.parametrize("pairs", [{"b": 2}, [("b", 2)]])
def test_positional_and_collision_compatibility(pairs):
    o = OneToOne({"a": 1})
    o.update(pairs, c=3)
    assert dict(o.inv) == {1: "a", 2: "b", 3: "c"}
    # update overwrites collisions on the pin; unique is the rejecting API.
    o.update({"d": 2})
    assert dict(o) == {"a": 1, "c": 3, "d": 2}
    assert dict(o.inv) == {1: "a", 3: "c", 2: "d"}
    with pytest.raises(ValueError):
        OneToOne.unique({"a": 1, "b": 1})
