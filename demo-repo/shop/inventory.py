"""Stock levels and reservations."""


class OutOfStock(Exception):
    """Raised when a reservation asks for more units than are free."""


def _check_quantity(quantity: int) -> None:
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")


class Inventory:
    def __init__(self, stock: dict[str, int] | None = None) -> None:
        self._stock = dict(stock or {})
        self._reserved: dict[str, int] = {}

    def available(self, sku: str) -> int:
        """Units in stock that are not reserved."""
        return self._stock.get(sku, 0) - self._reserved.get(sku, 0)

    def restock(self, sku: str, quantity: int) -> None:
        _check_quantity(quantity)
        self._stock[sku] = self._stock.get(sku, 0) + quantity

    def reserve(self, sku: str, quantity: int) -> None:
        """Hold units for an order. Fails if fewer are available."""
        if quantity > self.available(sku):
            raise OutOfStock(f"{sku}: wanted {quantity}, have {self.available(sku)}")
        self._reserved[sku] = self._reserved.get(sku, 0) + quantity

    def release(self, sku: str, quantity: int) -> None:
        """Give reserved units back; never releases more than is held."""
        _check_quantity(quantity)
        held = self._reserved.get(sku, 0)
        self._reserved[sku] = max(0, held - quantity)

    def low_stock(self, threshold: int = 5) -> list[str]:
        """SKUs whose available units are at or below the threshold."""
        return sorted(sku for sku in self._stock if self.available(sku) <= threshold)
