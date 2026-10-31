"""Value equality, identity preservation for other items and bounded consumption."""

from importlib import import_module
from itertools import count, islice

r = import_module("boltons.iterutils")


def test_examples_and_nonmutation():
    src = [1, 2, 1, 3]
    assert r.replaced(src, 1, 0) == [0, 2, 0, 3]
    assert list(r.replaced_iter(src, 1, 0)) == [0, 2, 0, 3]
    assert src == [1, 2, 1, 3]
    assert r.replaced("abca", "a", "z") == ["z", "b", "c", "z"]
    assert r.replaced([1.0, 2], 1, "x") == ["x", 2]
    assert r.replaced([], None, 1) == []
    old, equal, untouched, replacement = [7], [7], [8], object()
    result = r.replaced([equal, untouched], old, replacement)
    assert result[0] is replacement and result[1] is untouched


def test_lazy_iterator():
    assert next(r.replaced_iter(count(), 0, -1)) == -1
    seen = []

    def source():
        for item in count():
            seen.append(item)
            yield item

    iterator = r.replaced_iter(source(), 1, None)
    assert seen == []
    assert list(islice(iterator, 3)) == [0, None, 2]
    assert seen == [0, 1, 2]
    # Default windowing observation evaluated on the pin.
    assert r.windowed(range(3), 2) == [(0, 1), (1, 2)]
