"""Host readings and stable memory-hold messages, without depending on the host."""

from pathlib import Path

import pytest

from oversight.resources import hold_reason, is_hold, memory_used, parse_meminfo

MEMINFO = "MemTotal:       1000 kB\nMemFree:         20 kB\nMemAvailable:   101 kB\n"


@pytest.mark.parametrize(
    "text,expected",
    [
        (MEMINFO, 89),
        ("MemTotal: 100 kB\nMemAvailable: 101 kB", 0),
        ("MemTotal: 100 kB\nMemAvailable: -1 kB", 100),
        ("MemTotal: 100 kB", None),
        ("MemAvailable: 10 kB", None),
        ("MemTotal: 0 kB\nMemAvailable: 0 kB", None),
        ("MemTotal: bad kB\nMemAvailable: 10 kB", None),
        ("MemTotal: 100 kB\nMemAvailable: bad kB", None),
        ("MemTotal:\nMemAvailable: 10 kB", None),
        ("", None),
    ],
)
def test_parse_meminfo(text: str, expected: int | None) -> None:
    assert parse_meminfo(text) == expected


def test_memory_used_reads_file_and_fails_open(tmp_path: Path) -> None:
    path = tmp_path / "meminfo"
    path.write_text(MEMINFO, encoding="utf-8")
    assert memory_used(path) == 89
    assert memory_used(tmp_path / "missing") is None
    assert memory_used(tmp_path) is None
    path.write_bytes(b"\xff")
    assert memory_used(path) is None
    path.write_text("MemTotal: 0\nMemAvailable: 0", encoding="utf-8")
    assert memory_used(path) is None


@pytest.mark.parametrize("used", [None, 89, 90, 99])
def test_hold_reason(used: int | None) -> None:
    expected = "Waiting: memory at 90% or more" if used is not None and used >= 90 else None
    assert hold_reason(used, 90) == expected


def test_full_memory_threshold() -> None:
    assert hold_reason(99, 100) is None
    assert hold_reason(100, 100) == "Waiting: memory at 100% or more"


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Waiting: memory at 90% or more", True),
        ("Waiting: memory at 80% or more", True),
        ("main suspect", False),
        ("Waiting: slot A", False),
        ("prefix Waiting: memory at 90% or more", False),
        ("", False),
    ],
)
def test_is_hold(message: str, expected: bool) -> None:
    assert is_hold(message) is expected
