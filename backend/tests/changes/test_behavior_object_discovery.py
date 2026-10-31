"""Object discovery reuses source inventories and respects pytest's actual bindings."""

import ast
from collections import Counter

import pytest

from oversight.changes._behavior_inputs import behavior_inputs, calls
from oversight.changes._behavior_methods import method_inputs
from oversight.changes._shortcuts_common import Source

PRODUCT = "class Product:\n    def __init__(self, value): self.value = value\n"
FIXTURE = (
    "import pytest\nfrom sample import Product\n@pytest.fixture\ndef widget(): return Product(1)\n"
)


def files_for(test):
    return {
        "sample.py": PRODUCT + "def f(product): return product.value\n",
        "tests/conftest.py": FIXTURE,
        "tests/test_sample.py": "import pytest\nimport sample\n" + test,
    }


@pytest.mark.parametrize(
    "test",
    [
        "def test_x(widget=None): sample.f(widget)",
        "def test_x(*, widget=None): sample.f(widget)",
        "@pytest.mark.parametrize('widget', [None])\ndef test_x(widget): sample.f(widget)",
        "@pytest.mark.parametrize(('widget', 'x'), [(None, 1)])\n"
        "def test_x(widget, x): sample.f(widget)",
        "@pytest.mark.parametrize('x, widget', [(1, None)])\n"
        "def test_x(widget, x): sample.f(widget)",
        "@pytest.mark.parametrize(argnames='widget', argvalues=[None])\n"
        "def test_x(widget): sample.f(widget)",
        "@pytest.fixture(name='widget')\ndef local(): return None\n"
        "def test_x(widget): sample.f(widget)",
        "class TestShop:\n    @pytest.fixture\n    def widget(self): return None\n"
        "    def test_x(self, widget): sample.f(widget)",
        "class TestShop:\n    @pytest.fixture(name='widget')\n"
        "    def local(self): return None\n    def test_x(self, widget): sample.f(widget)",
        "@pytest.mark.parametrize('widget', [None])\nclass TestShop:\n"
        "    def test_x(self, widget): sample.f(widget)",
    ],
    ids=[
        "default",
        "kw-default",
        "param",
        "tuple-param",
        "csv-param",
        "keyword-param",
        "renamed-fixture",
        "class-fixture",
        "renamed-class-fixture",
        "class-param",
    ],
)
def test_non_fixture_test_bindings_refuse_parent_fallback(test):
    files = files_for(test)
    result = behavior_inputs("sample", "f", files, files)
    assert not result.inputs and result.reason == "required argument has no literal value"


def test_fixture_reference_in_signature_default_has_fixed_refusal():
    files = files_for("widget = 7\ndef test_x(widget, y=sample.f(widget)): pass")
    from oversight.changes._behavior_objects import arguments

    source = Source(files["tests/test_sample.py"])
    call = source.tests["test_x"].args.defaults[0]
    assert isinstance(call, ast.Call)
    with pytest.raises(ValueError, match=r"^rebound fixture parameter$"):
        arguments(source, "tests/test_sample.py", call, files)
    assert not behavior_inputs("sample", "f", files, files).inputs


def test_second_constructor_occurrence_reuses_all_parses(monkeypatch):
    files = files_for("sample.f(sample.Product(1))\nsample.f(sample.Product(2))")
    parsed = Counter()
    original = Source.__post_init__

    def counted(source):
        parsed[source.text] += 1
        original(source)

    monkeypatch.setattr(Source, "__post_init__", counted)
    discovered = calls(files, "sample", "f", float("inf"), objects=True)
    next(discovered)
    after_first = parsed.copy()
    next(discovered)
    assert parsed == after_first
    assert all(count == 1 for count in parsed.values())


@pytest.mark.parametrize("method", [False, True])
def test_each_side_parses_each_file_once_across_discovery_and_identity(monkeypatch, method):
    files = files_for("def test_x(widget):\n    sample.f(widget)\n    sample.f(sample.Product(2))")
    if method:
        files["sample.py"] += "class Cart:\n    def add(self, product): return product.value\n"
        files["tests/test_sample.py"] = (
            "import sample\ndef test_x(widget):\n    cart = sample.Cart()\n"
            "    cart.add(widget)\n    cart.add(sample.Product(2))"
        )
    parsed = Counter()
    original = Source.__post_init__

    def counted(source):
        parsed[source.text] += 1
        original(source)

    monkeypatch.setattr(Source, "__post_init__", counted)
    result = (method_inputs if method else behavior_inputs)(
        "sample", "Cart.add" if method else "f", files, files
    )
    assert result.reason is None and result.inputs
    assert parsed == Counter({text: 2 for text in files.values()})


@pytest.mark.parametrize("argument", ["sample.Product(1)", "widget"])
def test_setter_object_samples_keep_literal_inputs(argument):
    files = files_for(
        "def test_x(widget):\n    cart = sample.Cart()\n"
        f"    cart.handler({argument})\n    cart.handler(7)"
    )
    files["sample.py"] += (
        "class Cart:\n    def __init__(self): self._handler = 0\n"
        "    @property\n    def handler(self): return self._handler\n"
        "    @handler.setter\n    def handler(self, value): self._handler = value\n"
    )
    result = method_inputs("sample", "Cart.handler#2", files, files)
    assert result.reason is None
    assert any(sample.call == "Cart().handler = 7" for sample in result.inputs)
    assert any(sample.call == "Cart().handler = Product(1)" for sample in result.inputs)
