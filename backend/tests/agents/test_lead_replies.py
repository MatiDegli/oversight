"""Stored lead replies are masked before their exact wire bounds are checked."""

import pytest

from oversight.agents._lead_replies import MAX_REPLY_CHARS, TRUNCATED, reply_text
from oversight.redaction import Redactor


def test_mask_before_bounds_and_storage() -> None:
    mask = Redactor("secret" * 1000)
    assert reply_text("secret" * 1000, mask, strict=True) == "[redacted]"


def test_exact_bounds_and_server_truncation() -> None:
    assert reply_text("x" * 4096, str, strict=True) == "x" * 4096
    with pytest.raises(ValueError, match="4,096"):
        reply_text("x" * 4097, str, strict=True)
    shortened = reply_text("x" * 6000, str)
    assert shortened is not None and len(shortened) == MAX_REPLY_CHARS
    assert shortened.endswith(TRUNCATED)


@pytest.mark.parametrize("text", [None, "", " \n\t"])
def test_blank_text_is_nullable_only_for_cuts(text: str | None) -> None:
    assert reply_text(text, str) is None
    with pytest.raises(ValueError, match="nonblank"):
        reply_text(text, str, strict=True)
