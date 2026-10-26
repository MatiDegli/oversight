import pytest

from shop.cart import Cart
from shop.inventory import Inventory, OutOfStock
from shop.orders import cancel_order, place_order, reorder_quote, revenue_by_category


def test_place_order_prices_and_reserves(widget, gadget, customer, stocked):
    cart = Cart()
    cart.add(widget, 3)
    cart.add(gadget, 2)
    order = place_order(cart, stocked, customer, 1)
    assert order.shipping == 599
    assert order.total == 9239
    assert stocked.available("W-1") == 17
    assert stocked.available("G-1") == 3


def test_place_order_with_premium_discount_and_coupon(widget, premium, stocked):
    cart = Cart()
    cart.add(widget, 20)
    order = place_order(cart, stocked, premium, 2, coupon="SPRING10")
    assert order.shipping == 0
    assert order.total == 14580


def test_place_order_releases_earlier_lines_when_stock_runs_out(
    widget, gadget, customer
):
    inventory = Inventory({"W-1": 5, "G-1": 1})
    cart = Cart()
    cart.add(widget, 3)
    cart.add(gadget, 2)
    with pytest.raises(OutOfStock):
        place_order(cart, inventory, customer, 3)
    assert inventory.available("W-1") == 5
    assert inventory.available("G-1") == 1


def test_cancel_order_returns_the_stock(widget, gadget, customer, stocked):
    cart = Cart()
    cart.add(widget, 3)
    cart.add(gadget, 2)
    order = place_order(cart, stocked, customer, 4)
    cancel_order(order, stocked)
    assert stocked.available("W-1") == 20
    assert stocked.available("G-1") == 5


def test_reorder_quote_and_revenue_by_category(widget, gadget, customer, stocked):
    cart = Cart()
    cart.add(widget, 3)
    cart.add(gadget, 2)
    order = place_order(cart, stocked, customer, 5)
    assert reorder_quote(order) == 8000
    assert revenue_by_category([order]) == {"tools": 3000, "toys": 5000}
