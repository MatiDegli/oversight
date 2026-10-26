import pytest

from shop.models import format_cents


def test_format_cents():
    assert format_cents(1250) == "$12.50"
    assert format_cents(5) == "$0.05"
    assert format_cents(-250) == "-$2.50"


def test_product_label_and_price(widget):
    assert widget.label() == "Widget (W-1)"
    assert widget.price_for(20) == 18000


def test_email_is_lowercased_and_the_setter_rejects_bad_addresses(customer):
    assert customer.email == "ada@example.com"
    with pytest.raises(ValueError):
        customer.email = "not-an-email"
    assert customer.email == "ada@example.com"


def test_premium_customers_get_an_extra_discount(widget, customer, premium):
    assert customer.quote(widget, 20) == 18000
    assert premium.quote(widget, 20) == 15000
