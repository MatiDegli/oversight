"""Core data types shared by the rest of the shop."""

from dataclasses import dataclass

from .pricing import compute_price


def format_cents(cents: int) -> str:
    """Format an amount in cents as dollars, for example 1250 -> '$12.50'."""
    sign = "-" if cents < 0 else ""
    dollars, rest = divmod(abs(cents), 100)
    return f"{sign}${dollars}.{rest:02d}"


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    unit_price: int  # cents
    category: str = "general"

    def label(self) -> str:
        return f"{self.name} ({self.sku})"

    def price_for(self, quantity: int) -> int:
        """Price in cents for `quantity` units at the list price."""
        return compute_price(self.unit_price, quantity)


@dataclass
class OrderLine:
    product: Product
    quantity: int


class Customer:
    def __init__(self, name: str, email: str) -> None:
        self.name = name
        self._email = ""
        self.email = email

    @property
    def email(self) -> str:
        return self._email

    @email.setter
    def email(self, value: str) -> None:
        if "@" not in value:
            raise ValueError(f"invalid email address: {value!r}")
        self._email = value.lower()

    def discount_percent(self) -> int:
        """Percent taken off every order, on top of any bulk discount."""
        return 0

    def quote(self, product: Product, quantity: int) -> int:
        """Price in cents for this customer, with their own discount applied."""
        return compute_price(product.unit_price, quantity, self.discount_percent())


class PremiumCustomer(Customer):
    def discount_percent(self) -> int:
        return 15
