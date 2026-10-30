"""Decoration forms, validation and existing wrapping/building behavior."""

import inspect
from importlib import import_module

import pytest

f = import_module("boltons.funcutils")


def test_forms_and_metadata():
    @f.kwargs_decorator
    def tag(obj, label="x", a=0):
        """Attach options to a callable."""
        obj.label, obj.a = label, a
        return obj

    @tag
    def bare():
        pass

    @tag()
    def empty():
        pass

    @tag(label="y", a=3)
    def configured():
        pass

    assert [obj.label for obj in (bare, empty, configured)] == ["x", "x", "y"]
    assert configured.a == 3
    assert tag.__name__ == "tag" and tag.__doc__ == "Attach options to a callable."
    for args in ((3,), (None,), ("label",), (bare, empty), (bare, 3)):
        with pytest.raises(TypeError):
            tag(*args)


def test_wrapping_compatibility():
    assert callable(f.kwargs_decorator(lambda obj: obj))

    def original(x=9):
        """Original metadata."""
        return x + 2

    @f.wraps(original)
    def proxy(*args, **kwargs):
        return original(*args, **kwargs)

    # Signature, result and builder observations evaluated on the pin.
    assert (str(inspect.signature(proxy)), proxy(), proxy(3)) == ("(x=9)", 11, 5)
    assert (proxy.__name__, proxy.__doc__) == ("original", "Original metadata.")
    builder = f.FunctionBuilder("probe", args=["x"], defaults=(9,), body="return x + 2")
    assert (builder.get_sig_str(), builder.get_func()(), builder.get_func()(3)) == ("(x)", 11, 5)
