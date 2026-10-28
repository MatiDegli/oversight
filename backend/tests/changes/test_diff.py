"""Diff statistics preserve matching semantics without holding up the event loop."""

import asyncio
from collections.abc import Iterator
from threading import Event

import pytest

from oversight.changes import _diff


@pytest.fixture(autouse=True)
def empty_cache() -> Iterator[None]:
    with _diff._cache_lock:
        _diff._counts.clear()
    yield
    with _diff._cache_lock:
        _diff._counts.clear()


@pytest.mark.parametrize(
    ("original", "modified", "expected"),
    [
        (None, None, (0, 0)),
        (None, "a\nb\n", (2, 0)),
        ("a\nb\n", None, (0, 2)),
        ("", "", (0, 0)),
        ("a\n", "a", (0, 0)),
        ("a\nb\n", "a\nc\n", (1, 1)),
        ("a\nb\n", "b\na\n", (1, 1)),
        ("á\r\nb\r\n", "á\nb\n", (0, 0)),
        ("\ud800\n", "\ud800\nb\n", (1, 0)),
    ],
)
def test_cached_counts_preserve_line_semantics(
    original: str | None, modified: str | None, expected: tuple[int, int]
) -> None:
    assert _diff.count_lines(original, modified) == expected
    assert _diff.count_lines(original, modified) == expected


def test_cache_hits_same_pair_and_misses_changed_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    direct = _diff._count_lines

    def observed(original: str | None, modified: str | None) -> tuple[int, int]:
        calls.append((original, modified))
        return direct(original, modified)

    monkeypatch.setattr(_diff, "_count_lines", observed)
    assert _diff.count_lines("old\n", "new\n") == (1, 1)
    assert _diff.count_lines("old\n", "new\n") == (1, 1)
    assert calls == [("old\n", "new\n")]
    assert _diff.count_lines("old\n", "new\nextra\n") == (2, 1)
    assert _diff.count_lines("other\n", "new\n") == (1, 1)
    assert len(calls) == 3


def test_cache_is_bounded_and_evicts_least_recently_used() -> None:
    for index in range(_diff._CACHE_LIMIT):
        _diff.count_lines(None, f"{index}\n")
    oldest = (None, _diff._digest("0\n"))
    next_oldest = (None, _diff._digest("1\n"))
    _diff.count_lines(None, "0\n")
    _diff.count_lines(None, "new\n")
    assert len(_diff._counts) == _diff._CACHE_LIMIT
    assert oldest in _diff._counts
    assert next_oldest not in _diff._counts
    assert all(
        old is None and isinstance(new, bytes) and len(new) == 32 for old, new in _diff._counts
    )
    for index in range(_diff._CACHE_LIMIT * 2):
        _diff.count_lines("old\n", f"changed {index}\n")
        assert len(_diff._counts) == _diff._CACHE_LIMIT


@pytest.mark.anyio
async def test_repetitive_direct_threaded_and_cached_counts_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = "    return value\n" * 600
    modified = "    return value\n" * 300 + "    return changed\n" + "    return value\n" * 299
    direct = _diff._count_lines(original, modified)
    assert direct == (1, 1)  # autojunk=True would lose the repeated suffix
    calls = 0
    uncached = _diff._count_lines

    def observed(old: str | None, new: str | None) -> tuple[int, int]:
        nonlocal calls
        calls += 1
        return uncached(old, new)

    monkeypatch.setattr(_diff, "_count_lines", observed)
    assert await _diff.count_lines_async(original, modified) == direct  # cold, in a thread
    assert await asyncio.to_thread(_diff.count_lines, original, modified) == direct  # cached
    assert await _diff.count_lines_async(original, modified) == direct  # warm async path
    assert calls == 1


@pytest.mark.anyio
async def test_slow_count_allows_another_coroutine_to_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release, finished = Event(), Event()
    progressed = False

    def slow_count(original: str | None, modified: str | None) -> tuple[int, int]:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10), "the other coroutine never released the count"
        finished.set()
        return (2, 1)

    async def other() -> None:
        nonlocal progressed
        await entered.wait()
        assert not finished.is_set()
        progressed = True
        release.set()

    monkeypatch.setattr(_diff, "_count_lines", slow_count)
    task = asyncio.create_task(_diff.count_lines_async("old", "new"))
    try:
        async with asyncio.timeout(15):
            await other()
            assert await task == (2, 1)
            assert progressed
    finally:
        release.set()
        await task
