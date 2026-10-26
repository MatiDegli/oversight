from shop.pricing import (
    add_tax,
    apply_coupon,
    compute_price,
    coupon_percent,
    price_breaks,
    shipping_cost,
)


def test_small_quantities_have_no_discount():
    assert compute_price(1000, 1) == 1000
    assert compute_price(1000, 9) == 9000


def test_twenty_units_get_the_first_tier():
    assert compute_price(1000, 20) == 18000


def test_forty_nine_units_still_get_the_first_tier():
    assert compute_price(1000, 49) == 44100


def test_sixty_units_get_the_second_tier():
    assert compute_price(1000, 60) == 48000


def test_extra_percent_stacks_with_the_tier():
    assert compute_price(1000, 20, 15) == 15000


def test_price_breaks_lists_sample_quantities():
    assert price_breaks(1000) == {1: 1000, 5: 5000, 20: 18000}


def test_coupons_ignore_case_and_unknown_codes():
    assert coupon_percent("welcome5") == 5
    assert coupon_percent(" SPRING10 ") == 10
    assert coupon_percent("nope") == 0
    assert apply_coupon(2000, "WELCOME5") == 1900


def test_tax_is_added_and_shipping_is_free_from_the_threshold():
    assert add_tax(1000) == 1080
    assert shipping_cost(9999) == 599
    assert shipping_cost(10_000) == 0
