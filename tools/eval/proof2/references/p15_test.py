"""Finite boundary matrix, including unsorted nonuniform options."""

import math
from importlib import import_module

import pytest

ceil = import_module("boltons.mathutils").ceil
clamp = import_module("boltons.mathutils").clamp
floor = import_module("boltons.mathutils").floor


@pytest.mark.parametrize("x", [-5, -2, 0, 7, 11])
def test_clamp_bounds(x):
    assert clamp(x, -2, 7) == (-2 if x < -2 else 7 if x > 7 else x)
    assert clamp(x, 3, 3) == 3


@pytest.mark.parametrize("x", [-4, -2, 0, 3, 7, 10, 12])
def test_nonuniform_options(x):
    options = [10, -2, 7, 0]
    above, below = [v for v in options if v >= x], [v for v in options if v <= x]
    for func, candidates, choose in ((ceil, above, min), (floor, below, max)):
        if candidates:
            assert func(x, options) == choose(candidates)
        else:
            with pytest.raises(ValueError):
                func(x, options)
    assert options == [10, -2, 7, 0]
    assert ceil(x / 3) == math.ceil(x / 3)
    assert floor(x / 3) == math.floor(x / 3)
