"""An oversized ledger is diagnosable through the CLI without a traceback."""

import sys
from pathlib import Path

import pytest

from oversight.__main__ import main
from oversight.app import ledger_command
from oversight.limits import MAX_FILE_BYTES
from oversight.settings import Settings


def test_oversized_ledger_cli_names_file_and_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    path = runtime / "ledger.json"
    path.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    monkeypatch.setenv("OVERSIGHT_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("OVERSIGHT_PORT", "8710")
    monkeypatch.setenv("OVERSIGHT_VITE_PORT", "")
    monkeypatch.setattr(sys, "argv", ["oversight", "ledger"])
    main()
    output = capsys.readouterr()
    assert "ledger.json exceeds" in output.out
    assert "ledger settle --spent-usd X" in output.out
    assert output.err == ""
    assert path.stat().st_size == MAX_FILE_BYTES + 1
    assert ledger_command(Settings(runtime_dir=runtime), 1.0)
    assert path.stat().st_size <= MAX_FILE_BYTES
