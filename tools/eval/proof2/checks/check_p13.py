"""One pass, one item ahead, including a final None value."""

from importlib import import_module
from itertools import count

look = import_module("boltons.iterutils")


def test_examples_and_empty():
    assert list(look.lookahead("abc")) == [("a", False), ("b", False), ("c", True)]
    assert list(look.lookahead([])) == []
    assert list(look.lookahead([7])) == [(7, True)]
    assert next(look.lookahead(count())) == (0, False)
    assert list(look.lookahead([None, None])) == [(None, False), (None, True)]


def test_single_pass_and_consumption():
    class SinglePass:
        def __init__(self):
            self.iterations, self.reads = 0, 0

        def __iter__(self):
            self.iterations += 1
            assert self.iterations == 1
            return self

        def __next__(self):
            if self.reads == 4:
                raise StopIteration
            self.reads += 1
            return self.reads

    source = SinglePass()
    iterator = look.lookahead(source)
    assert source.reads <= 1
    assert next(iterator) == (1, False) and source.reads == 2
    assert next(iterator) == (2, False) and source.reads == 3
    assert list(iterator) == [(3, False), (4, True)]
    assert source.iterations == 1
    # Default windowing observation evaluated on the pin.
    assert look.windowed(range(3), 2) == [(0, 1), (1, 2)]
