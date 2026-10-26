import pytest

from shop.cart import Cart


def test_add_merges_lines_with_the_same_sku(widget):
    cart = Cart()
    cart.add(widget, 3)
    cart.add(widget, 4)
    assert [(line.product.sku, line.quantity) for line in cart.lines()] == [("W-1", 7)]


def test_add_rejects_quantities_below_one(widget):
    with pytest.raises(ValueError):
        Cart().add(widget, 0)


def test_remove_drops_the_line_and_describe_lists_the_rest(widget, gadget):
    cart = Cart()
    cart.add(widget, 2)
    cart.add(gadget)
    cart.remove("W-1")
    cart.remove("missing")
    assert cart.describe() == ["1 x Gadget (G-1): $25.00"]


def test_total_applies_the_bulk_discount(widget, gadget):
    cart = Cart()
    cart.add(widget, 20)
    cart.add(gadget, 2)
    assert cart.subtotals() == [18000, 5000]
    assert cart.total() == 23000


def test_quote_add_prices_the_extra_units_across_a_tier(widget):
    cart = Cart()
    cart.add(widget, 8)
    assert cart.quote_add(widget, 4) == 2800
