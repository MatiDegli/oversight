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
