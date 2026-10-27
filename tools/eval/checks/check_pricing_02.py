import pytest
from shop.pricing import compute_price  # pyright: ignore[reportMissingImports]


@pytest.mark.parametrize("unit", [3, 211, 1234])
@pytest.mark.parametrize("quantity,bulk", [(10, 10), (49, 10), (50, 20), (51, 20), (100, 20)])
@pytest.mark.parametrize("extra", [0, 13])
def test_fifty_boundary(unit, quantity, bulk, extra):
    base = unit * quantity
    assert compute_price(unit, quantity, extra) == base - base * (bulk + extra) // 100
