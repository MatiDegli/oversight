"""Character cuts and word-mode observations captured on d31d821."""

from importlib import import_module

import pytest

s = import_module("boltons.strutils")


@pytest.mark.parametrize(
    "text,limit,suffix,expected",
    [
        ("123456789", 8, "...", "12345..."),
        ("123456789", 9, "...", "123456789"),
        ("Hello, World!", 9, "…", "Hello, W…"),
        ("ab,;! xyz", 6, "…", "ab,;!…"),
        ("é猫 a bc", 4, "..", "é猫.."),
        ("abcdef", 3, "", "abc"),
        ("", 2, "…", ""),
    ],
)
def test_character_cut(text, limit, suffix, expected):
    assert s.ellipsize(text, limit, ellipsis=suffix, by="char") == expected


@pytest.mark.parametrize("by", ["words", "CHAR", "", None, 0, []])
def test_invalid_mode_even_for_short_text(by):
    with pytest.raises(ValueError):
        s.ellipsize("", 9, by=by)


def test_word_compatibility_and_length_errors():
    # These results were evaluated on the pinned implementation, before writing references.
    cases = [
        ("Hello, World!", 9, "…", "Hello…"),
        ("rates around 6.5% this week", 20, "…", "rates around 6.5%…"),
        ("alpha, beta! gamma", 13, "..", "alpha.."),
        ("antidisestablishmentarianism", 10, "…", "antidises…"),
        (" a  b; c d", 8, "…", " a  b…"),
        ("tiny", 8, "…", "tiny"),
    ]
    for text, limit, suffix, expected in cases:
        assert s.ellipsize(text, limit, ellipsis=suffix) == expected
        assert s.ellipsize(text, limit, ellipsis=suffix, by="word") == expected
    for mode in ("word", "char"):
        for limit in (-2, 0, 3):
            with pytest.raises(ValueError):
                s.ellipsize("", limit, ellipsis="...", by=mode)
