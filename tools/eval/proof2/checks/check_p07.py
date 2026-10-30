"""Truth testing is unrelated to iteration; None/default verified on d31d821."""

from importlib import import_module

import pytest

BarrelList = import_module("boltons.listutils").BarrelList


class Iterable:
    def __init__(self, raises):
        self.raises = raises

    def __bool__(self):
        if self.raises:
            raise RuntimeError("do not truth-test an iterable")
        return False

    def __iter__(self):
        return iter([0, None, "last"])


@pytest.mark.parametrize("raises", [False, True])
def test_false_or_raising_bool_keeps_items(raises):
    source = Iterable(raises)
    try:
        actual = list(BarrelList(source))
    except RuntimeError as error:
        pytest.fail(str(error))
    assert actual == list(source)


def test_empty_and_ordinary_compatibility():
    assert list(BarrelList()) == list(BarrelList(None)) == []
    assert list(BarrelList([0, 2, 4])) == [0, 2, 4]
