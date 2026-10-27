import pytest
from shop.inventory import Inventory, OutOfStock  # pyright: ignore[reportMissingImports]


@pytest.mark.parametrize("quantity", [0, -1, -7, -100])
@pytest.mark.parametrize("sku", ["A", "missing"])
def test_invalid_reservations_leave_inventory_unchanged(quantity, sku):
    inventory = Inventory({"A": 23})
    inventory.reserve("A", 7)
    before = inventory.available("A"), inventory.available("missing")
    with pytest.raises(ValueError):
        inventory.reserve(sku, quantity)
    assert (inventory.available("A"), inventory.available("missing")) == before
    inventory.release("A", 7)
    assert inventory.available("A") == 23
    with pytest.raises(OutOfStock):
        inventory.reserve("A", 24)
    inventory.reserve("A", 23)
    assert inventory.available("A") == 0
