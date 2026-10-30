"""Index protocol on forced partitions; integer observations verified on d31d821."""

from importlib import import_module

import pytest

BarrelList = import_module("boltons.listutils").BarrelList


class Index:
    def __init__(self, value):
        self.value = value

    def __index__(self):
        return self.value


@pytest.mark.parametrize("position", [0, 3, 7, -1, -12, -99, 99])
@pytest.mark.parametrize("wrapped", [False, True])
def test_partitioned_insert(position, wrapped):
    values = list(range(12))
    barrel = BarrelList(values)
    barrel.lists = [values[:3], values[3:7], values[7:]]
    assert len(barrel.lists) == 3
    index = Index(position) if wrapped else position
    values.insert(index, 99)
    barrel.insert(index, 99)
    assert list(barrel) == values
