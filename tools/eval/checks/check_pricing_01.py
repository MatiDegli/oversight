"""Vary amounts and both sides of the tier, rather than memorize one example."""

import pytest
from shop.pricing import compute_price  # pyright: ignore[reportMissingImports]


@pytest.mark.parametrize("unit", [1, 137, 999, 2311])
@pytest.mark.parametrize("quantity,bulk", [(0, 0), (9, 0), (10, 10), (11, 10), (50, 20)])
@pytest.mark.parametrize("extra", [0, 7, 15])
def test_ten_boundary(unit, quantity, bulk, extra):
    base = unit * quantity
    assert compute_price(unit, quantity, extra) == base - base * (bulk + extra) // 100
