import pytest
from shop.inventory import Inventory, OutOfStock  # pyright: ignore[reportMissingImports]


@pytest.mark.parametrize("stock,held,excess", [(1, 0, 1), (17, 3, 2), (43, 29, 100)])
def test_failed_attempt_preserves_prior_reservations(stock, held, excess):
    inventory = Inventory({"A": stock, "B": 7})
    if held:
        inventory.reserve("A", held)
    before = stock - held
    for _ in range(3):
        with pytest.raises(OutOfStock):
            inventory.reserve("A", before + excess)
        assert inventory.available("A") == before
        assert inventory.available("B") == 7
    inventory.reserve("A", before)
    assert inventory.available("A") == 0
    inventory.release("A", before + held)
    assert inventory.available("A") == stock
    with pytest.raises(OutOfStock):
        inventory.reserve("missing", 2)
    assert inventory.available("missing") == 0
    inventory.restock("missing", 3)
    inventory.reserve("missing", 2)
    assert inventory.available("missing") == 1
