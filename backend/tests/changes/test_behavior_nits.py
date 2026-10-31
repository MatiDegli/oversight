"""Regression coverage for private probe failures and conservative object identities."""

import ast
from unittest.mock import AsyncMock

import pytest
from changes_helpers import EDIT, Harness

from oversight.changes import _proposal
from oversight.changes._behavior_construction import construction_ast
from oversight.changes._behavior_inputs import behavior_inputs
from oversight.changes._behavior_objects import arguments
from oversight.changes._shortcuts_common import Source
from oversight_contracts.review import ChangeFacts

PRODUCT = "class Product:\n    def __init__(self, value): self.value = value\n"
FIXTURE = (
    "import pytest\nfrom sample import Product\n@pytest.fixture\ndef widget(): return Product(1)\n"
)


@pytest.fixture
def session_c() -> bool:
    return True


@pytest.mark.anyio
async def test_unexpected_probe_exception_publishes_worker_failure(h: Harness, monkeypatch):
    probe = AsyncMock(side_effect=LookupError("private probe diagnostic"))
    monkeypatch.setattr(_proposal, "probe_behavior", probe)
    h.manager._ctx.behavior_diff = True
    await h.run_agent(1, EDIT)
    probe.assert_awaited_once()
    [facts] = h.payloads(ChangeFacts)
    assert facts.behavior_diff
    assert {item.not_probed for item in facts.behavior_diff} == {"probe failed"}
    rec = h.manager._agents["agent-1"]
    assert {item.reason for item in rec.behavior_diffs[1].functions} == {"worker failed"}
    assert "LookupError" not in facts.model_dump_json()
    assert "private probe diagnostic" not in facts.model_dump_json()


@pytest.mark.parametrize("renamed", [False, True])
@pytest.mark.parametrize("location", ["outer", "inner"])
def test_nested_class_fixture_blocks_module_and_conftest_fallback(renamed, location):
    decorator = "@pytest.fixture(name='widget')" if renamed else "@pytest.fixture"
    fixture = f"{decorator}\ndef {'local' if renamed else 'widget'}(self): return None\n"
    indent = "    " if location == "outer" else "        "
    indented = "".join(indent + line + "\n" for line in fixture.splitlines())
    test = "class TestOuter:\n"
    test += indented if location == "outer" else ""
    test += "    class TestInner:\n"
    test += indented if location == "inner" else ""
    test += "        def test_x(self, widget): sample.f(widget)\n"
    files = {
        "sample.py": PRODUCT + "def f(product): return product.value\n",
        "tests/test_sample.py": FIXTURE + "import sample\n" + test,
        "tests/conftest.py": FIXTURE,
    }
    source = Source(files["tests/test_sample.py"])
    call = source.tests["TestOuter.TestInner.test_x"].body[0]
    assert isinstance(call, ast.Expr) and isinstance(call.value, ast.Call)
    with pytest.raises(ValueError, match=r"^unsupported class fixture$"):
        arguments(source, "tests/test_sample.py", call.value, files)
    result = behavior_inputs("sample", "f", files, files)
    assert not result.inputs
    assert result.reason == "required argument has no literal value"


@pytest.mark.parametrize("binding_failure", [False, True])
def test_sample_rejections_preserve_binding_failures(binding_failure):
    # Base-side objects bind, but their proposed-side class is unsupported.
    files = {
        "sample.py": PRODUCT + "def f(product): return product.value\n",
        "tests/test_sample.py": (
            "from sample import f, Product\nf(Product(1))\n" + ("f()\n" if binding_failure else "")
        ),
    }
    head = {
        **files,
        "sample.py": files["sample.py"].replace("class Product:", "class Product(Base):"),
    }
    generated = behavior_inputs("sample", "f", files, head)
    assert not generated.inputs
    assert generated.reason == (
        "argument binding failed; unsupported signature"
        if binding_failure
        else "unsupported signature"
    )


@pytest.mark.parametrize(
    "signature", ["widget, y=sample.f(widget)", "widget, *, y=sample.f(widget)"]
)
@pytest.mark.parametrize("enclosing", [False, True])
def test_fixture_in_own_signature_default_is_refused_without_keyerror(signature, enclosing):
    test = f"def test_x({signature}): pass\n"
    if enclosing:
        test = "class TestOuter:\n    class TestInner:\n        " + test.replace(
            "def test_x(", "def test_x(self, "
        )
    files = {
        "sample.py": PRODUCT + "def f(product): return product.value\n",
        "tests/test_sample.py": "import sample\n" + test,
        "tests/conftest.py": FIXTURE,
    }
    source = Source(files["tests/test_sample.py"])
    [test_node] = source.tests.values()
    call = (test_node.args.defaults or test_node.args.kw_defaults)[0]
    assert isinstance(call, ast.Call)
    with pytest.raises(ValueError, match=r"^rebound fixture parameter$"):
        arguments(source, "tests/test_sample.py", call, files)
    generated = behavior_inputs("sample", "f", files, files)
    assert not generated.inputs
    assert generated.reason == "required argument has no literal value"


@pytest.mark.parametrize(
    "product",
    [
        "class Product:\n    def __init__(self, value): self.value = normalize(value)\n",
        """class Product:
    def __init__(self, value): self.value = value
    @property
    def value(self): return self._value
    @value.setter
    def value(self, value): self._value = normalize(value)
""",
        """def get_value(self): return self._value
def set_value(self, value): self._value = normalize(value)
class Product:
    def __init__(self, value): self.value = value
    value = property(get_value, set_value)
""",
        """def get_value(self): return normalize(self._value)
def set_value(self, value): self._value = value
class Product:
    def __init__(self, value): self.value = value; self.snapshot = self.value
    value = property(fget=get_value, fset=set_value)
""",
        """class Product:
    def __init__(self, value): self.value = normalize(value); self.snapshot()
    def snapshot(self): return 9
""",
    ],
    ids=["init", "setter", "module-property-setter", "module-property-getter", "fallback"],
)
@pytest.mark.parametrize("changed_helper", ["direct", "transitive"])
def test_module_construction_helpers_invalidate_input_identity(product, changed_helper):
    helpers = (
        "def normalize(value): return round_value(value)\ndef round_value(value): return value\n"
    )
    source = helpers + product + "def f(product): return product.value\n"
    files = {
        "sample.py": source,
        "tests/test_sample.py": "from sample import f, Product\nf(Product(1))\n",
    }
    unchanged = behavior_inputs("sample", "f", files, files)
    assert unchanged.inputs and all(item.input_identity is not None for item in unchanged.inputs)
    edit = "return round_value(value)" if changed_helper == "direct" else "return value\n"
    head = {**files, "sample.py": source.replace(edit, edit.rstrip() + " + 1\n")}
    changed = behavior_inputs("sample", "f", files, head)
    assert changed.inputs and all(item.input_identity is None for item in changed.inputs)


def test_helper_cycles_terminate_and_unreached_helpers_do_not_change_identity():
    source = (
        "def normalize(value): return round_value(value)\n"
        "def round_value(value): return normalize(value)\n"
        "def unrelated(value): return 9\n"
        "class Product:\n    def __init__(self, value): self.value = normalize(value)\n"
        "def f(product): return product.value\n"
    )
    files = {
        "sample.py": source,
        "tests/test_sample.py": "from sample import f, Product\nf(Product(1))\n",
    }
    head = {**files, "sample.py": "\n\n" + source.replace("return 9", "return 10")}
    generated = behavior_inputs("sample", "f", files, head)
    assert generated.inputs and all(item.input_identity is not None for item in generated.inputs)


@pytest.mark.parametrize("accessor", ["get_value", "set_value"])
def test_module_property_accessor_source_is_part_of_identity(accessor):
    source = (
        "def get_value(self): return self._value\n"
        "def set_value(self, value): self._value = value\n"
        "class Product:\n    def __init__(self, value): self.value = value\n"
        "    value = property(get_value, set_value)\n"
        "def f(product): return product.value\n"
    )
    files = {
        "sample.py": source,
        "tests/test_sample.py": "from sample import f, Product\nf(Product(1))\n",
    }
    old = "return self._value" if accessor == "get_value" else "self._value = value"
    head = {**files, "sample.py": source.replace(old, old + " + 1")}
    generated = behavior_inputs("sample", "f", files, head)
    assert generated.inputs and all(item.input_identity is None for item in generated.inputs)


def test_transitive_module_helper_walk_checks_existing_deadline(monkeypatch):
    from oversight.changes import _behavior_construction as construction

    source = Source(
        "def normalize(value): return round_value(value)\n"
        "def round_value(value): return value\n"
        "class Product:\n    def __init__(self, value): self.value = normalize(value)\n"
    )
    deadline = float("inf")
    walking_transitive_helper = False
    original_walk = ast.walk

    def walk(node):
        nonlocal walking_transitive_helper
        if isinstance(node, ast.FunctionDef) and node.name == "round_value":
            walking_transitive_helper = True
        return original_walk(node)

    def check(received):
        assert received == deadline
        if walking_transitive_helper:
            raise TimeoutError("analysis deadline")

    monkeypatch.setattr(ast, "walk", walk)
    monkeypatch.setattr(construction, "check_deadline", check)
    with pytest.raises(TimeoutError, match="analysis deadline"):
        construction_ast(source.classes["Product"], deadline, source)
    assert walking_transitive_helper
