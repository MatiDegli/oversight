"""Placing, cancelling and reporting on orders."""

from dataclasses import dataclass

from . import pricing
from .cart import Cart
from .inventory import Inventory, OutOfStock
from .models import Customer, OrderLine


@dataclass
class Order:
    order_id: int
    customer: Customer
    lines: list[OrderLine]
    total: int
    shipping: int = 0
    cancelled: bool = False


def order_total(lines: list[OrderLine], customer: Customer) -> int:
    """Items total after bulk and customer discounts, before coupon, tax and shipping."""
    percent = customer.discount_percent()
    return sum(
        pricing.compute_price(line.product.unit_price, line.quantity, percent)
        for line in lines
    )


def _release_lines(lines: list[OrderLine], inventory: Inventory) -> None:
    for line in lines:
        inventory.release(line.product.sku, line.quantity)


def place_order(
    cart: Cart,
    inventory: Inventory,
    customer: Customer,
    order_id: int,
    coupon: str = "",
) -> Order:
    """Reserve stock for every line, then price the order. All or nothing."""
    lines = cart.lines()
    reserved: list[OrderLine] = []
    try:
        for line in lines:
            inventory.reserve(line.product.sku, line.quantity)
            reserved.append(line)
    except OutOfStock:
        _release_lines(reserved, inventory)
        raise
    items = pricing.apply_coupon(order_total(lines, customer), coupon)
    shipping = pricing.shipping_cost(items)
    total = pricing.add_tax(items) + shipping
    return Order(order_id, customer, lines, total, shipping)


def cancel_order(order: Order, inventory: Inventory) -> None:
    """Return an order's stock. Cancelling an already cancelled order does nothing."""
    if order.cancelled:
        return
    order.cancelled = True
    _release_lines(order.lines, inventory)


def reorder_quote(order: Order) -> int:
    """Items total if the same lines were ordered again, at today's prices."""
    return sum(
        order.customer.quote(line.product, line.quantity) for line in order.lines
    )


def revenue_by_category(orders: list[Order]) -> dict[str, int]:
    """Discounted revenue per product category, before coupon, tax and shipping."""
    totals: dict[str, int] = {}
    for order in orders:
        percent = order.customer.discount_percent()
        for line in order.lines:
            price = pricing.compute_price(
                line.product.unit_price, line.quantity, percent
            )
            category = line.product.category
            totals[category] = totals.get(category, 0) + price
    return totals
