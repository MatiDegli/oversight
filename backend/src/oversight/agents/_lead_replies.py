"""Mask reply text before validating or shortening anything stored in the thread."""

from collections.abc import Callable

MAX_REPLY_CHARS = 4_096
TRUNCATED = "\n[Reply truncated by the server.]"


def reply_text(text: str | None, mask: Callable[[str], str], *, strict: bool = False) -> str | None:
    masked = mask(text or "")
    if not masked.strip():
        if strict:
            raise ValueError("reply must be nonblank")
        return None
    if len(masked) > MAX_REPLY_CHARS:
        if strict:
            raise ValueError("masked reply exceeds 4,096 characters")
        masked = masked[: MAX_REPLY_CHARS - len(TRUNCATED)] + TRUNCATED
    return masked
