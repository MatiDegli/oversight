"""Stride selection against a slicing oracle, including sparse windows."""

from importlib import import_module
from itertools import count, islice

import pytest

w = import_module("boltons.iterutils")


@pytest.mark.parametrize(
    "length,size,step",
    [
        (7, 3, 2),
        (5, 3, 2),
        (7, 2, 3),
        (0, 3, 2),
        (2, 4, 3),
        (9, 1, 4),
        (8, 3, 7),
        (6, 3, 1),
        (4, 2, 10**30),
    ],
)
def test_window_matrix(length, size, step):
    src = list(range(length))
    full = [tuple(src[i : i + size]) for i in range(0, max(0, length - size + 1), step)]
    filled = [tuple((src[i : i + size] + [None] * size)[:size]) for i in range(0, length, step)]
    for fn in (w.windowed, w.windowed_iter):
        assert list(fn(iter(src), size, step=step)) == full
        assert list(fn(iter(src), size, fill=None, step=step)) == filled
    assert src == list(range(length))


@pytest.mark.parametrize("step", [0, -1, 1.5, "2", None, False])
def test_invalid_stride(step):
    for fn in (w.windowed, w.windowed_iter):
        with pytest.raises(ValueError):
            list(fn([], 3, step=step))


def test_infinite_and_default_compatibility():
    assert list(islice(w.windowed_iter(count(), 3, step=2), 2)) == [(0, 1, 2), (2, 3, 4)]
    assert list(islice(w.windowed_iter(count(), 2, fill=None, step=3), 2)) == [(0, 1), (3, 4)]
    # The following default results were evaluated on the pin.
    assert w.windowed(range(7), 3) == [(0, 1, 2), (1, 2, 3), (2, 3, 4), (3, 4, 5), (4, 5, 6)]
    assert w.windowed(range(2), 4) == []
    assert w.windowed(range(2), 4, fill=None) == [(0, 1, None, None), (1, None, None, None)]
