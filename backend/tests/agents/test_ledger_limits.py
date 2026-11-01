"""Ledger writes remain readable and failed writes preserve the previous spend."""

import json
from pathlib import Path

import pytest

from oversight.agents import LedgerError, LedgerFile, LedgerState, UnknownUsage
from oversight.limits import MAX_FILE_BYTES


def test_compact_ledger_round_trips_below_read_cap(tmp_path: Path) -> None:
    ledger = LedgerFile(tmp_path / "ledger.json")
    entry = UnknownUsage("run", "agent-1", None, "2026-10-27T14:00:00.000Z")
    state = LedgerState(2.0, (entry,) * 2200)
    ledger.write(state)
    body = json.loads(ledger.path.read_text())
    assert len(json.dumps(body, indent=2).encode()) > MAX_FILE_BYTES
    assert ledger.path.stat().st_size <= MAX_FILE_BYTES
    assert ledger.read() == state


def test_oversized_ledger_write_preserves_all_previous_entries(tmp_path: Path) -> None:
    ledger = LedgerFile(tmp_path / "ledger.json")
    entry = UnknownUsage("run", "agent-1", None, "2026-10-27T14:00:00.000Z")
    previous = LedgerState(3.0, (entry,))
    ledger.write(previous)
    before = ledger.path.read_bytes()
    with pytest.raises(LedgerError, match=r"ledger.json exceeds .* settle the ledger"):
        ledger.write(LedgerState(4.0, (entry,) * 4000))
    assert ledger.path.read_bytes() == before
    assert ledger.read() == previous
    assert list(tmp_path.iterdir()) == [ledger.path]


@pytest.mark.parametrize("exhaust", [False, True])
def test_nt_replace_retry_preserves_ledger(tmp_path, monkeypatch, exhaust):
    import os
    import time
    from types import SimpleNamespace

    from oversight.agents import usage

    ledger = LedgerFile(tmp_path / "ledger.json")
    ledger.write(LedgerState(3.0, ()))
    previous = ledger.path.read_bytes()
    replace = os.replace
    attempts = []

    class SharingError(PermissionError):
        winerror = 32

    first = SharingError("sharing")

    def sharing(*args):
        attempts.append(args)
        if exhaust or len(attempts) < 3:
            raise first
        return replace(*args)

    monkeypatch.setattr(usage, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(os, "replace", sharing)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    if exhaust:
        with pytest.raises(PermissionError) as caught:
            ledger.write(LedgerState(4.0, ()))
        assert caught.value is first and len(attempts) == 7
        assert ledger.path.read_bytes() == previous
    else:
        ledger.write(LedgerState(4.0, ()))
        assert len(attempts) == 3 and ledger.read().spent_usd == 4.0
    assert list(tmp_path.iterdir()) == [ledger.path]


def test_nt_ledger_cleanup_failure_surfaces(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from oversight.agents import usage

    ledger = LedgerFile(tmp_path / "ledger.json")
    ledger.write(LedgerState(3.0, ()))
    monkeypatch.setattr(usage, "sys", SimpleNamespace(platform="win32"))

    def fail_replace(*args, **kwargs):
        raise PermissionError("replace")

    def fail_unlink(*args, **kwargs):
        raise PermissionError("cleanup")

    monkeypatch.setattr(usage, "replace_retry", fail_replace)
    monkeypatch.setattr(usage, "unlink_retry", fail_unlink)
    with pytest.raises(PermissionError, match="cleanup"):
        ledger.write(LedgerState(4.0, ()))
    assert ledger.read().spent_usd == 3.0
