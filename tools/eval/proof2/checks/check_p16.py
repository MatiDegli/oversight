"""Concrete types, metaclasses and existing sentinel invariants."""

from collections import OrderedDict
from copy import copy, deepcopy
from importlib import import_module

t = import_module("boltons.typeutils")


def test_concrete_type_names():
    class Meta(type):
        pass

    class Custom(metaclass=Meta):
        pass

    Disguised = type("Disguised", (), {"__class__": property(lambda self: int)})

    for obj, expected in [
        (1, "int"),
        (None, "NoneType"),
        (int, "type"),
        (OrderedDict(), "OrderedDict"),
        (True, "bool"),
        ([], "list"),
        (Custom, "Meta"),
        (Custom(), "Custom"),
        (Disguised(), "Disguised"),
    ]:
        assert t.typename(obj) == expected


def test_sentinels_unchanged():
    sentinel = t.make_sentinel("MARK")
    assert t.typename(sentinel) == "Sentinel"
    # Observations evaluated on the pin, including uniqueness across calls.
    assert repr(sentinel) == "Sentinel('MARK')" and not sentinel
    assert copy(sentinel) is sentinel and deepcopy(sentinel) is sentinel
    assert sentinel != t.make_sentinel("MARK")
