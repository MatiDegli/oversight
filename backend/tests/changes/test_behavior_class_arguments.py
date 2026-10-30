"""Project objects are finite constructor data, never expressions evaluated by the parent."""

import ast
import json
import sys
from pathlib import Path

import pytest

from oversight.changes._behavior_inputs import behavior_inputs
from oversight.changes._behavior_methods import method_inputs
from oversight.workspace import run_process
from oversight.workspace.behavior_run import WORKER
from oversight.workspace.behavior_worker import _arguments

PRODUCT = """class Product:
    def __init__(self, sku, name='Widget', price=1000, category='tools'):
        self.sku, self.name, self.price, self.category = sku, name, price, category
"""
FUNCTION = "def f(product): return product.price\n"
FIXTURE = """import pytest
from sample import Product
@pytest.fixture
def widget():
    return Product('W-1', 'Widget', 1000, 'tools')
"""


def prepared(
    test="f(Product('W-1'))", *, product=PRODUCT, fixture=None, path="tests/test_sample.py"
):
    files = {"sample.py": product + FUNCTION, path: "from sample import Product, f\n" + test + "\n"}
    if fixture is not None:
        files["tests/conftest.py"] = fixture
    return files, behavior_inputs("sample", "f", files, files)


async def worker(root, files, inputs, *, method=False, repeats=2, module="sample"):
    root.mkdir(parents=True, exist_ok=True)
    for path, text in files.items():
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
    target = {"id": "f", "module": module, "name": "f", "calls": list(inputs) * repeats}
    if method:
        target.update(receiver_class="Cart", method_kind="method", name="add")
    request, report = root / "request.json", root / "report.json"
    request.write_text(json.dumps([target]))
    process = await run_process(
        [sys.executable, "-B", str(WORKER), str(root), str(request), str(report), "0.2"],
        cwd=root,
        timeout_s=10,
        env={"HOME": str(root)},
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout == process.stderr == ""
    return json.loads(report.read_text())["f"]["results"]


@pytest.mark.anyio
async def test_direct_constructor_is_data_displayed_and_fresh_on_both_sides(tmp_path):
    files, generated = prepared("f(Product('W-1', price=700))")
    files["sample.py"] = files["sample.py"].replace(
        FUNCTION, "def f(product):\n    product.price += 1\n    return product.price\n"
    )
    assert generated.reason is None
    sample = generated.inputs[0]
    assert sample.call == "f(product=Product('W-1', price=700))"
    assert sample.input_identity is not None
    data = ast.literal_eval(sample.before)
    assert data[0] == "oversight.object_arguments.v1"
    assert data[2]["product"] == ("constructor", "sample", "Product", ("W-1",), {"price": 700})
    for side in ("base", "head"):
        rows = await worker(tmp_path / side, files, [sample.before])
        assert rows[0] == rows[1]
        assert rows[0]["value"] == ["int", hex(701)]
        assert "state" not in rows[0]


@pytest.mark.parametrize("location", ["same", "directory", "parent", "root"])
def test_simple_fixture_scopes_and_constructor_aliases(location):
    path = "tests/unit/test_sample.py"
    test = "from sample import f\ndef test_price(widget):\n    f(widget)\n"
    fixture = FIXTURE.replace("import Product", "import Product as Item").replace(
        "return Product(", "return Item("
    )
    files = {"sample.py": PRODUCT + FUNCTION, path: test}
    fixture_path = {
        "same": path,
        "directory": "tests/unit/conftest.py",
        "parent": "tests/conftest.py",
        "root": "conftest.py",
    }[location]
    files[fixture_path] = fixture + test if fixture_path == path else fixture
    generated = behavior_inputs("sample", "f", files, files)
    assert generated.reason is None
    assert generated.inputs[0].call == "f(product=Product('W-1', 'Widget', 1000, 'tools'))"


@pytest.mark.parametrize(
    "fixture",
    [
        FIXTURE.replace("@pytest.fixture", "@pytest.fixture(params=[1, 2])"),
        FIXTURE.replace("return Product", "yield Product"),
        FIXTURE.replace("def widget()", "def widget(other)"),
        FIXTURE.replace("1000", "500 + 500"),
        FIXTURE.replace("return Product", "x = 1\n    return Product"),
        FIXTURE.replace("return Product('W-1', 'Widget', 1000, 'tools')", "return widget"),
        FIXTURE + "\nwidget = lambda: None\n",
    ],
    ids=["params", "yield", "dependency", "expression", "statements", "not-constructor", "rebound"],
)
def test_unsupported_fixture_is_refused(fixture):
    _, result = prepared("def test_price(widget):\n    f(widget)", fixture=fixture)
    assert not result.inputs and result.reason == "unsupported signature"


@pytest.mark.parametrize(
    "product",
    [
        "class Meta(type): pass\n"
        + PRODUCT.replace("class Product:", "class Product(metaclass=Meta):"),
        PRODUCT + "    def __setattr__(self, name, value): pass\n",
        "class Parent: pass\n" + PRODUCT.replace("class Product:", "class Product(Parent):"),
        PRODUCT.replace("self.sku,", "print('effect')\n        self.sku,"),
    ],
    ids=["metaclass", "setattr", "bases", "effect"],
)
def test_parent_reuses_receiver_refusals(product):
    _, result = prepared(product=product)
    assert not result.inputs and result.reason == "unsupported signature"


def test_test_module_class_is_refused():
    files = {
        "sample.py": FUNCTION,
        "tests/test_sample.py": "from sample import f\n" + PRODUCT + "f(Product('W-1'))\n",
    }
    assert not behavior_inputs("sample", "f", files, files).inputs


@pytest.mark.parametrize(
    "argument",
    ["Product(Product('W-1'))", "[Product('W-1')]", "widget.price", "make_product()", "widget"],
)
def test_only_top_level_literal_constructors_and_fixture_parameters_are_supported(argument):
    _, result = prepared(f"f({argument})")
    assert not result.inputs


@pytest.mark.parametrize(
    "test",
    [
        "def test_price(widget):\n    widget = 3\n    f(widget)",
        "def helper(widget):\n    f(widget)",
        "def test_price(widget):\n    [f(widget) for widget in []]",
        "def test_price(widget):\n    (lambda widget: f(widget))(3)",
        "def test_price(widget):\n    def test_inner(widget):\n        f(widget)",
    ],
)
def test_fixture_names_must_remain_unshadowed_test_parameters(test):
    _, result = prepared(test, fixture=FIXTURE)
    assert not result.inputs


@pytest.mark.parametrize(
    "imports,call",
    [
        ("from sample import Product as Item, f", "f(Item('W-1'))"),
        ("import sample as s\nf = s.f\nItem = s.Product", "f(Item('W-1'))"),
    ],
)
def test_direct_constructor_uses_existing_alias_resolver(imports, call):
    files = {"sample.py": PRODUCT + FUNCTION, "tests/test_sample.py": imports + "\n" + call}
    assert behavior_inputs("sample", "f", files, files).inputs


@pytest.mark.parametrize("binding", ["Product", "Item"])
def test_shadowed_constructor_is_refused(binding):
    _, result = prepared(
        f"from sample import Product as Item\ndef test_price({binding}):\n    f({binding}('W-1'))"
    )
    assert not result.inputs


def test_closest_fixture_override_blocks_parent_fallback():
    files, _ = prepared("def test_price(widget):\n    f(widget)", fixture=FIXTURE)
    files["tests/test_sample.py"] += (
        "\nimport pytest\n@pytest.fixture\ndef widget(other): return other\n"
    )
    assert not behavior_inputs("sample", "f", files, files).inputs


def test_fixture_in_sibling_directory_is_not_visible():
    files, _ = prepared("def test_price(widget):\n    f(widget)")
    files["other/conftest.py"] = FIXTURE
    assert not behavior_inputs("sample", "f", files, files).inputs


def test_identity_includes_module_class_literals_and_constructor_defaults():
    files, before = prepared()
    head = {**files, "sample.py": files["sample.py"].replace("price=1000", "price=2000")}
    changed = behavior_inputs("sample", "f", files, head)
    assert changed.inputs and all(sample.input_identity is None for sample in changed.inputs)
    assert before.inputs[0].input_identity is not None
    other_files, other = prepared("f(Product('G-1'))")
    assert other_files and other.inputs[0].input_identity != before.inputs[0].input_identity
    other_files = {**files, "other.py": PRODUCT}
    other_files["tests/test_sample.py"] = (
        "from sample import f\nfrom other import Product\nf(Product('W-1'))"
    )
    different_module = behavior_inputs("sample", "f", other_files, other_files)
    assert different_module.inputs[0].input_identity != before.inputs[0].input_identity


@pytest.mark.anyio
async def test_method_objects_are_fresh_and_only_receiver_post_state_is_reported(tmp_path):
    source = (
        PRODUCT
        + """class Cart:
    def __init__(self): self.total = 0
    def add(self, product):
        product.price += 1
        self.total += product.price
        return self.total
"""
    )
    files = {
        "sample.py": source,
        "tests/test_sample.py": "from sample import Cart, Product\nCart().add(Product('W-1'))",
    }
    result = method_inputs("sample", "Cart.add", files, files)
    assert result.reason is None
    rows = await worker(tmp_path, files, [result.inputs[0].before], method=True)
    assert rows[0] == rows[1]
    assert rows[0]["value"] == ["int", hex(1001)]
    assert rows[0]["state"]["stored"] == [["total", ["int", hex(1001)]]]


@pytest.mark.anyio
async def test_demo_cart_add_has_widget_and_gadget_inputs(tmp_path):
    root = Path(__file__).parents[3] / "demo-repo"
    files = {str(path.relative_to(root)): path.read_text() for path in root.rglob("*.py")}
    generated = method_inputs("shop.cart", "Cart.add", files, files)
    assert generated.reason is None
    assert any("Product('W-1', 'Widget', 1000, 'tools')" in item.call for item in generated.inputs)
    assert any("Product('G-1', 'Gadget', 2500, 'toys')" in item.call for item in generated.inputs)
    assert all(item.input_identity is not None for item in generated.inputs)
    selected = [
        next(item.before for item in generated.inputs if sku in item.call) for sku in ("W-1", "G-1")
    ]
    rows = await worker(tmp_path, files, selected, method=True, module="shop.cart")
    assert all(row["kind"] in ("value", "raised") for row in rows)
    assert rows[:2] == rows[2:]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        "Product.__setattr__ = lambda *args: None",
        "Product.__module__ = 'outside'",
        "class Meta(type): pass\nProduct = Meta('Product', (), {})",
    ],
)
async def test_worker_refuses_class_mutation_missed_by_parent(tmp_path, mutation):
    files, _ = prepared()
    files["sample.py"] += mutation + "\n"
    generated = behavior_inputs("sample", "f", files, files)
    assert generated.inputs  # A syntactically plain class can change while its module imports.
    rows = await worker(tmp_path, files, [generated.inputs[0].before])
    assert all(
        row == {"kind": "unrepresentable", "reason": "argument class mismatch"} for row in rows
    )


def test_worker_class_mismatch_has_fixed_reason(tmp_path, monkeypatch):
    import types

    module = types.ModuleType("sample")
    module.__file__ = str(tmp_path / "sample.py")
    exec(PRODUCT, module.__dict__)
    module.Product.__setattr__ = lambda *args: None
    monkeypatch.setitem(sys.modules, "sample", module)
    _, generated = prepared()
    with pytest.raises(ValueError, match=r"^argument class mismatch$"):
        _arguments(generated.inputs[0].before, tmp_path)


def test_object_discovery_obeys_deadline(monkeypatch):
    from oversight.changes import _behavior_objects as objects

    original = objects.constructor
    completed_first = False

    def first_candidate(*args, **kwargs):
        nonlocal completed_first
        result = original(*args, **kwargs)
        completed_first = True
        return result

    def check(_deadline):
        if completed_first:
            raise TimeoutError("analysis timeout")

    monkeypatch.setattr(objects, "constructor", first_candidate)
    monkeypatch.setattr(objects, "check_deadline", check, raising=False)
    files = {
        "sample.py": PRODUCT + "def f(first, second): return first.price\n",
        "tests/test_sample.py": "from sample import f, Product\nf(Product('W'), Product('G'))",
    }
    with pytest.raises(TimeoutError):
        behavior_inputs("sample", "f", files, files)
    assert completed_first


def test_object_and_literal_protocol_lookalikes_never_share_an_identity():
    from oversight.changes._behavior_objects import Constructor, input_identity
    from oversight.changes._review_identity import bound_inputs

    files, objects = prepared()
    effective = bound_inputs({"sku": "W-1", "name": "Widget", "price": 1000, "category": "tools"})
    lookalike = ("constructor", "sample", "Product", ("W-1",), {}, effective)
    instance = Constructor("sample", "Product", ("W-1",), {})
    identity = input_identity({"product": instance}, files, float("inf"))
    assert identity == objects.inputs[0].input_identity
    assert identity != input_identity({"product": lookalike}, files, float("inf"))


@pytest.mark.anyio
async def test_constructed_argument_is_not_deepcopied(tmp_path):
    product = PRODUCT + "    def __deepcopy__(self, memo): raise RuntimeError('must not clone')\n"
    files, generated = prepared(product=product)
    rows = await worker(tmp_path, files, [generated.inputs[0].before])
    assert all(row["kind"] == "value" for row in rows)


def test_invalid_unrelated_python_does_not_hide_constructor_arguments():
    files, _ = prepared()
    files["broken.py"] = "def invalid("
    assert behavior_inputs("sample", "f", files, files).inputs


def test_async_fixture_override_does_not_fall_back_to_parent():
    files, _ = prepared("def test_price(widget):\n    f(widget)", fixture=FIXTURE)
    files["tests/test_sample.py"] += "\n" + FIXTURE.replace("def widget()", "async def widget()")
    assert not behavior_inputs("sample", "f", files, files).inputs


@pytest.mark.parametrize(
    "decorator", ["@pytest.fixture()", "@pytest.fixture(scope='session', autouse=True)"]
)
def test_fixture_options_without_parameters_are_supported(decorator):
    _, result = prepared(
        "def test_price(widget):\n    f(widget)",
        fixture=FIXTURE.replace("@pytest.fixture", decorator),
    )
    assert result.inputs


@pytest.mark.parametrize(
    "call",
    ["[cart.add(Product('W-1')) for cart in []]", "(lambda cart: cart.add(Product('W-1')))(None)"],
)
def test_assigned_receiver_must_not_be_shadowed(call):
    source = PRODUCT + "class Cart:\n    def add(self, product): return product.price\n"
    files = {
        "sample.py": source,
        "tests/test_sample.py": (
            "from sample import Cart, Product\ndef test_price():\n    cart = Cart()\n    " + call
        ),
    }
    result = method_inputs("sample", "Cart.add", files, files)
    assert not result.inputs


@pytest.mark.anyio
async def test_object_construction_shares_the_existing_call_timeout(tmp_path):
    product = "class Product:\n    def __init__(self, sku):\n        while True: pass\n"
    files, generated = prepared(product=product)
    rows = await worker(tmp_path, files, [generated.inputs[0].before])
    assert all(row == {"kind": "timed_out"} for row in rows)
