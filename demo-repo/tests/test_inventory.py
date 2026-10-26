import pytest

from shop.inventory import OutOfStock


def test_reserve_reduces_available(stocked):
    stocked.reserve("W-1", 8)
    assert stocked.available("W-1") == 12


def test_reserve_more_than_available_raises(stocked):
    with pytest.raises(OutOfStock):
        stocked.reserve("G-1", 6)
    assert stocked.available("G-1") == 5


def test_release_returns_units_but_never_more_than_held(stocked):
    stocked.reserve("W-1", 8)
    stocked.release("W-1", 3)
    assert stocked.available("W-1") == 15
    stocked.release("W-1", 12)
    assert stocked.available("W-1") == 20


def test_restock_adds_units_and_rejects_non_positive_quantities(stocked):
    stocked.restock("W-1", 5)
    stocked.restock("N-1", 4)
    assert stocked.available("W-1") == 25
    assert stocked.available("N-1") == 4
    with pytest.raises(ValueError):
        stocked.restock("W-1", 0)
    with pytest.raises(ValueError):
        stocked.release("W-1", -1)


def test_low_stock_lists_skus_at_or_below_the_threshold(stocked):
    stocked.reserve("W-1", 18)
    assert stocked.low_stock() == ["G-1", "W-1"]
    assert stocked.low_stock(threshold=2) == ["W-1"]
