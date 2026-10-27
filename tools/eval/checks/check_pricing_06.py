import pytest
from shop.pricing import apply_coupon, coupon_percent  # pyright: ignore[reportMissingImports]


@pytest.mark.parametrize(
    "code,percent", [("welcome5", 5), ("SpRiNg10", 10), ("UNKNOWN", 0), ("", 0)]
)
@pytest.mark.parametrize("space", ["", " ", "\t", "\n  "])
def test_normalization(code, percent, space):
    normalized = space + code + space
    assert coupon_percent(normalized) == percent
    for total in [0, 17, 2307, 9999]:
        assert apply_coupon(total, normalized) == total - total * percent // 100
