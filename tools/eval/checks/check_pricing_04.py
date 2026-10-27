import pytest
from shop.pricing import compute_price  # pyright: ignore[reportMissingImports]


@pytest.mark.parametrize("unit", [0, 1, 317])
@pytest.mark.parametrize("quantity", [-1, -3, -50, -10000])
def test_negative_quantities_raise_even_for_free_products(unit, quantity):
    with pytest.raises(ValueError):
        compute_price(unit, quantity, 15)


@pytest.mark.parametrize("extra", [0, 15, 100])
def test_zero_is_allowed(extra):
    assert compute_price(673, 0, extra) == 0
    assert compute_price(673, 3, 15) == 2019 - 2019 * 15 // 100
