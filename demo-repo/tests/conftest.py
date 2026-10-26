import pytest

from shop.inventory import Inventory
from shop.models import Customer, PremiumCustomer, Product


@pytest.fixture
def widget() -> Product:
    return Product("W-1", "Widget", 1000, "tools")


@pytest.fixture
def gadget() -> Product:
    return Product("G-1", "Gadget", 2500, "toys")


@pytest.fixture
def customer() -> Customer:
    return Customer("Ada", "Ada@Example.com")


@pytest.fixture
def premium() -> PremiumCustomer:
    return PremiumCustomer("Grace", "grace@example.com")


@pytest.fixture
def stocked() -> Inventory:
    return Inventory({"W-1": 20, "G-1": 5})
