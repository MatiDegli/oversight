"""A shopping cart that merges lines by SKU."""

from .models import OrderLine, Product, format_cents
from .pricing import compute_price


class Cart:
    def __init__(self) -> None:
        self._lines: dict[str, OrderLine] = {}

    def add(self, product: Product, quantity: int = 1) -> None:
        """Add units of a product, merging with an existing line."""
        if quantity < 1:
            raise ValueError(f"quantity must be at least 1, got {quantity}")
        line = self._lines.get(product.sku)
        if line is None:
            self._lines[product.sku] = OrderLine(product, quantity)
        else:
            line.quantity += quantity

    def remove(self, sku: str) -> None:
        self._lines.pop(sku, None)

    def lines(self) -> list[OrderLine]:
        """Copies of the lines, so callers cannot change the cart by accident."""
        return [OrderLine(line.product, line.quantity) for line in self._lines.values()]

    def subtotals(self) -> list[int]:
        return [
            compute_price(line.product.unit_price, line.quantity)
            for line in self._lines.values()
        ]

    def total(self) -> int:
        return sum(self.subtotals())

    def quote_add(self, product: Product, quantity: int) -> int:
        """Extra cost of adding units; bulk tiers make it differ from the unit price."""
        line = self._lines.get(product.sku)
        have = line.quantity if line else 0
        after = compute_price(product.unit_price, have + quantity)
        before = compute_price(product.unit_price, have)
        return after - before

    def describe(self) -> list[str]:
        return [
            f"{line.quantity} x {line.product.label()}: "
            f"{format_cents(line.product.price_for(line.quantity))}"
            for line in self._lines.values()
        ]
