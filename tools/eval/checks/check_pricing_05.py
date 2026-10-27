import pytest
from shop.pricing import compute_price  # pyright: ignore[reportMissingImports]


@pytest.mark.parametrize("unit", [1, 317, 1599])
@pytest.mark.parametrize("quantity,bulk", [(0, 0), (3, 0), (10, 10), (53, 20)])
@pytest.mark.parametrize("extra", [7, 79, 80, 90, 100, 101, 217])
def test_total_discount_is_capped(unit, quantity, bulk, extra):
    base = unit * quantity
    assert compute_price(unit, quantity, extra) == base - base * min(bulk + extra, 100) // 100
