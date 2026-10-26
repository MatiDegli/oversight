"""Pricing rules: bulk discounts, coupons, tax and shipping. Amounts are in cents."""

from functools import lru_cache

# (minimum quantity, percent off), highest tier first.
BULK_TIERS = ((50, 20), (10, 10))
TAX_PERCENT = 8
FLAT_SHIPPING = 599
FREE_SHIPPING_FROM = 10_000

_COUPONS = {"WELCOME5": 5, "SPRING10": 10}


def compute_price(unit_price: int, quantity: int, extra_percent: int = 0) -> int:
    """Price of `quantity` units: the bulk tier and `extra_percent` are both taken off."""
    base = unit_price * quantity
    percent = extra_percent
    for minimum, tier_percent in BULK_TIERS:
        if quantity >= minimum:
            percent += tier_percent
            break
    return base - base * percent // 100


def price_breaks(
    unit_price: int, quantities: tuple[int, ...] = (1, 5, 20)
) -> dict[int, int]:
    """Price for a few sample quantities, handy for a price list."""
    return {quantity: compute_price(unit_price, quantity) for quantity in quantities}


@lru_cache(maxsize=256)
def coupon_percent(code: str) -> int:
    """Percent off for a coupon code; unknown codes are worth nothing."""
    return _COUPONS.get(code.strip().upper(), 0)


def apply_coupon(total: int, code: str) -> int:
    percent = coupon_percent(code)
    return total - total * percent // 100


def add_tax(total: int) -> int:
    return total + total * TAX_PERCENT // 100


def shipping_cost(total: int) -> int:
    """Flat rate, free once the items reach the threshold."""
    return 0 if total >= FREE_SHIPPING_FROM else FLAT_SHIPPING
