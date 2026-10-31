"""Credential masking shared by local event storage and development probes."""

import re

FRAGMENT = 8  # a run of this many characters of the key is as good as the key
REDACTED = "[redacted]"
# What looks like a credential whoever it belongs to: a bearer token, an sk-style key, or a long
# run of letters and digits mixed (hyphens and underscores split words, so slugs and field names
# are left alone).
KEY_LIKE = re.compile(
    r"Bearer\s+[A-Za-z0-9._~+/=-]{6,}"
    r"|\b(?:sk|pk|rk)[-_][A-Za-z0-9_-]{8,}"
    r"|\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{24,}\b",
    re.IGNORECASE,
)


class Redactor:
    """Mask credentials in text destined for local storage or probe reports.

    It removes the key, every run of FRAGMENT or more characters that appears in the key (so a
    reflected piece of it goes too, not only the whole), and anything shaped like a credential.
    Calling it on its own output changes nothing.
    """

    def __init__(self, key: str | None, *other_keys: str | None) -> None:
        self._windows: dict[int, set[str]] = {}
        for text in (key, *other_keys):
            if text:
                width = min(FRAGMENT, len(text))
                self._windows.setdefault(width, set()).update(
                    text[i : i + width] for i in range(len(text) - width + 1)
                )

    def __call__(self, text: str) -> str:
        return REDACTED.join(self._mask(piece) for piece in text.split(REDACTED))

    def _mask(self, text: str) -> str:
        spans: list[list[int]] = []
        for start in range(len(text)):
            width = max(
                (w for w, windows in self._windows.items() if text[start : start + w] in windows),
                default=0,
            )
            if width:
                if spans and start <= spans[-1][1]:
                    spans[-1][1] = max(spans[-1][1], start + width)
                else:
                    spans.append([start, start + width])
        pieces: list[str] = []
        done = 0
        for begin, end in spans:
            pieces += [text[done:begin], REDACTED]
            done = end
        pieces.append(text[done:])
        return KEY_LIKE.sub(REDACTED, "".join(pieces))
