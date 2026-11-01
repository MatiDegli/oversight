"""Stateless host-memory admission hints; unavailable readings never hold a start."""

from pathlib import Path

HOLD_PREFIX = "Waiting: memory at "


def parse_meminfo(text: str) -> int | None:
    """Floor used memory as a percentage of total, using Linux's available estimate."""
    values: dict[str, int] = {}
    try:
        for line in text.splitlines():
            key, _, value = line.partition(":")
            if key in {"MemTotal", "MemAvailable"}:
                values[key] = int(value.split()[0])
        total, available = values["MemTotal"], values["MemAvailable"]
    except (ValueError, IndexError, KeyError):
        return None
    if total <= 0 or available < 0:
        return None
    return max(0, min(100, (total - available) * 100 // total))


def memory_used(path: Path = Path("/proc/meminfo")) -> int | None:
    """Read the host inline; unsupported hosts and unreadable files fail open."""
    try:
        return parse_meminfo(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return None


def hold_reason(used: int | None, limit: int) -> str | None:
    """Keep the threshold stable in queue details even when the reading changes."""
    return f"{HOLD_PREFIX}{limit}% or more" if used is not None and used >= limit else None


def is_hold(message: str) -> bool:
    return message.startswith(HOLD_PREFIX)
