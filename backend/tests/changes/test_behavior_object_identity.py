"""Construction code changes invalidate object inputs rather than reporting false differences."""

import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from oversight.changes._behavior import FunctionBehavior, _compare
from oversight.changes._behavior_inputs import behavior_inputs
from oversight.workspace import BehaviorObservation

PRODUCT = """class Product:
    def __init__(self, value): self.value = value
    @property
    def value(self): return self._value
    @value.setter
    def value(self, value): self._value = self.normalize(value)
    def normalize(self, value): return value
    def unrelated(self): return 9
"""
RENAMED_SETTER = (
    PRODUCT.replace("def value(self, value):", "def _set_value(self, value):")
    + "    value = _set_value\n"
)
RENAMED_DELETER = (
    PRODUCT.replace("self.value = value", "self._value = value; del self.value")
    + """    @value.deleter
    def _delete_value(self): self._value = 0
    value = _delete_value
"""
)
ASSIGNED_SETTER = (
    PRODUCT.replace("    @value.setter\n", "").replace(
        "def value(self, value):", "def _set_value(self, value):"
    )
    + "    value = value.setter(_set_value)\n"
)
ASSIGNED_PROPERTY = """class Product:
    def __init__(self, value): self.value = value
    def _get(self): return self._value
    def _set(self, value): self._value = self.normalize(value)
    def normalize(self, value): return value
    value = property(_get, _set)
"""
READ_PROPERTY = (
    ASSIGNED_PROPERTY.replace(
        "self.value = value", "self._value = value; self._value = self.snapshot()"
    )
    + "    def snapshot(self): return self.value\n"
)


def inputs(before, after):
    base = {
        "sample.py": before + "def f(product): return product.value\n",
        "tests/test_sample.py": "from sample import f, Product\nf(Product(1))\n",
    }
    return behavior_inputs(
        "sample", "f", base, {**base, "sample.py": after + "def f(product): return product.value\n"}
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "before,after",
    [
        (PRODUCT, PRODUCT.replace("self.value = value", "self.value = value + 1")),
        (
            PRODUCT,
            PRODUCT.replace("self._value = self.normalize(value)", "self._value = value + 1"),
        ),
        (
            RENAMED_SETTER,
            RENAMED_SETTER.replace(
                "self._value = self.normalize(value)", "self._value = value + 1"
            ),
        ),
        (
            RENAMED_SETTER,
            RENAMED_SETTER.replace("return value\n", "return value + 1\n"),
        ),
        (
            RENAMED_DELETER,
            RENAMED_DELETER.replace("self._value = 0", "self._value = 1"),
        ),
        (
            ASSIGNED_SETTER,
            ASSIGNED_SETTER.replace(
                "self._value = self.normalize(value)", "self._value = value + 1"
            ),
        ),
        (
            ASSIGNED_SETTER,
            ASSIGNED_SETTER.replace("return value\n", "return value + 1\n"),
        ),
        (
            ASSIGNED_PROPERTY,
            ASSIGNED_PROPERTY.replace(
                "self._value = self.normalize(value)", "self._value = value + 1"
            ),
        ),
        (
            READ_PROPERTY,
            READ_PROPERTY.replace("return self._value\n", "return self._value + 1\n"),
        ),
        *[
            (bound, bound.replace("self._value = self.normalize(value)", "self._value = value + 1"))
            for bound in (
                ASSIGNED_PROPERTY.replace("value = property", "value: property = property"),
                ASSIGNED_PROPERTY.replace(
                    "value = property(_get, _set)", "value, other = property(_get, _set), 0"
                ),
            )
        ],
        (
            PRODUCT,
            PRODUCT.replace(
                "def normalize(self, value): return value",
                "def normalize(self, value): return value + 1",
            ),
        ),
        (
            PRODUCT.replace("class Product:", "class Product:\n    category = 1"),
            PRODUCT.replace("class Product:", "class Product:\n    category = 2"),
        ),
        (
            PRODUCT.replace("class Product:", "class Product:\n    category: int = 1"),
            PRODUCT.replace("class Product:", "class Product:\n    category: int = 2"),
        ),
        (
            PRODUCT.replace("self.value = value", "getattr(self, 'normalize')(value)"),
            PRODUCT.replace("self.value = value", "getattr(self, 'normalize')(value)").replace(
                "return 9", "return 10"
            ),
        ),
    ],
    ids=[
        "initializer",
        "setter",
        "renamed-setter",
        "renamed-setter-transitive-method",
        "renamed-deleter",
        "assigned-setter",
        "assigned-setter-transitive-method",
        "assigned-property-setter",
        "assigned-property-transitive-getter",
        "annotated-property-setter",
        "unpacked-property-setter",
        "transitive-method",
        "assignment",
        "annotated-field",
        "dynamic-fallback",
    ],
)
async def test_changed_construction_is_not_measured(before, after):
    generated = inputs(before, after)
    assert generated.reason is None and generated.inputs
    assert all(sample.input_identity is None for sample in generated.inputs)
    observations = [
        {
            "sample:f": BehaviorObservation(
                tuple(
                    {"kind": "value", "value": ["int", hex(value)], "fingerprint": str(value)}
                    for _ in generated.inputs
                )
            )
        }
        for value in (1, 2)
    ]
    worker = AsyncMock(side_effect=observations)
    rec = SimpleNamespace(
        head=SimpleNamespace(
            graph=SimpleNamespace(
                nodes=[SimpleNamespace(id="sample:f", path="sample.py", name="f")]
            )
        )
    )
    found = [FunctionBehavior("sample:f")]
    await _compare(
        cast(Any, SimpleNamespace(run_behavior=worker)),
        cast(Any, rec),
        cast(Any, ["base", "head"]),
        {"sample:f": generated},
        found,
        time.monotonic() + 10,
        set(),
    )
    assert found[0].compared == found[0].differing == 0
    assert found[0].reason == "no equal typed inputs"
    assert worker.await_count == 2  # Unequal inputs do not spend any repeat calls.


def test_unrelated_method_and_line_numbers_do_not_change_construction_identity():
    generated = inputs(PRODUCT, "\n\n" + PRODUCT.replace("return 9", "return 10"))
    assert generated.inputs and all(
        sample.input_identity is not None for sample in generated.inputs
    )
