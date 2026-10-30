"""Closed interval operations, independent finite cases and iterator inputs."""

from importlib import import_module

import pytest

m = import_module("boltons.mathutils")


@pytest.mark.parametrize(
    "pairs,union,intersection",
    [
        ([(1, 5), (2, 6), (1, 4)], [(1, 6)], (2, 4)),
        ([(1, 5), (6, 8), (7, 9)], [(1, 5), (6, 9)], None),
        ([(1, 5), (5, 8)], [(1, 8)], (5, 5)),
        ([(4, 4), (-3, 0), (-1, 4), (8, 9)], [(-3, 4), (8, 9)], None),
        ([(2.5, 6), (-1, 8), (2.5, 6)], [(-1, 8)], (2.5, 6)),
        ([(0, 0)], [(0, 0)], (0, 0)),
    ],
)
def test_ranges(pairs, union, intersection):
    original = [list(p) for p in reversed(pairs)]
    saved = [p[:] for p in original]
    assert m.range_union(original) == union
    assert m.range_intersection(original) == intersection
    assert original == saved
    assert m.range_union(iter(pairs)) == union
    assert m.range_intersection(iter(pairs)) == intersection


@pytest.mark.parametrize("bad", [(5, 1), (), (1,), (1, 2, 3), None, (None, 2)])
def test_invalid_range_is_not_skipped(bad):
    for fn in (m.range_union, m.range_intersection):
        with pytest.raises(ValueError):
            fn(iter([(0, 1), (8, 9), bad]))


def test_empty_and_existing_math():
    assert m.range_union(iter([])) == []
    with pytest.raises(ValueError):
        m.range_intersection(iter([]))
    # Observations evaluated on the pin, including unsorted option handling.
    assert [m.clamp(x, -2, 4) for x in (-3, 0, 7)] == [-2, 0, 4]
    assert (m.ceil(3, [9, 2, 4]), m.floor(3, [9, 2, 4])) == (4, 2)
