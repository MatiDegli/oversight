import pytest
from shop.models import PremiumCustomer, Product  # pyright: ignore[reportMissingImports]
from shop.pricing import compute_price  # pyright: ignore[reportMissingImports]


@pytest.mark.parametrize("unit", [101, 673, 1789])
@pytest.mark.parametrize("quantity,bulk", [(1, 0), (10, 10), (23, 10), (50, 20), (71, 20)])
@pytest.mark.parametrize("extra", [7, 15, 31])
def test_discounts_add_before_rounding(unit, quantity, bulk, extra):
    base = unit * quantity
    assert compute_price(unit, quantity, extra) == base - base * (bulk + extra) // 100
    premium = PremiumCustomer("P", "p@example.org")
    assert premium.quote(Product("X", "Other", unit), quantity) == base - base * (bulk + 15) // 100
