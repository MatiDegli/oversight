"""Offline reports expose global totals, exact JSON, local offsets and refusals."""

import json
from dataclasses import asdict
from datetime import UTC
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfoNotFoundError

import pytest

import run_periods
from oversight.events import read_jsonl
from oversight.events.usage_periods import fold_periods
from oversight_contracts.events import AgentStatus, AgentStatusEvent, UsageTick, UsageTickEvent


@pytest.fixture
def log(tmp_path: Path) -> Path:
    start = AgentStatusEvent(
        seq=1,
        ts="2026-11-01T00:30:00.000Z",
        type="agent.status",
        payload=AgentStatus(
            agent_id="agent-1",
            slot="A",
            change_id="change-1",
            task="Implement",
            model="muse-spark-1.3-contributor",
            state="working",
            step=0,
            max_steps=10,
            detail=None,
        ),
    )
    tick = UsageTickEvent(
        seq=2,
        ts="2026-11-01T01:00:00.000Z",
        type="usage.tick",
        payload=UsageTick(
            agent_id="agent-1",
            model="muse-spark-1.3-contributor",
            input_tokens=1_000_000,
            cached_tokens=400_000,
            output_tokens=1_000_000,
            cost_usd=None,
            spent_usd=999,
            budget_usd=1000,
            brake="ok",
        ),
    )
    end = start.model_copy(
        update={
            "seq": 3,
            "ts": "2026-11-01T02:30:00.000Z",
            "payload": start.payload.model_copy(update={"state": "done"}),
        }
    )
    path = tmp_path / "recorded.jsonl"
    path.write_text("".join(e.model_dump_json() + "\n" for e in (start, tick, end)))
    return path


def test_text(log: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_periods.main([str(log), "--tz", "UTC", "--by", "hour"]) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert captured.err == ""
    assert len(lines) == 5 and lines[0] == "Run periods: UTC, by hour"
    assert lines[1] == (
        "2026-11-01T00:00:00+00:00; length 3600000 ms; 0.500000 agent-hours; "
        "0.500000 active hours; parallelism 1.000000; US$0.000000 list-price equivalent; "
        "0 unpriced, 0 ranged calls"
    )
    assert "US$0.260800" in lines[2] and "US$0.000000" in lines[3]
    assert lines[-1].startswith(
        "Totals: 2.000000 agent-hours; 2.000000 active hours; parallelism 1.000000; "
        "US$0.260800 list-price equivalent; activity complete; cost complete; "
    )
    assert "2026-10-30 from https://dev.meta.ai/docs/pricing-rate-limits" in lines[-1]
    assert "at least" not in captured.out and "—" not in captured.out


@pytest.mark.parametrize("unit", ["hour", "day"])
def test_json(log: Path, capsys: pytest.CaptureFixture[str], unit: Literal["hour", "day"]) -> None:
    assert run_periods.main([str(log), "--tz", "UTC", "--by", unit, "--json"]) == 0
    captured = capsys.readouterr()
    expected = asdict(fold_periods(read_jsonl(log), UTC, unit))
    assert captured.out == json.dumps(expected, sort_keys=True) + "\n"
    assert captured.err == ""


def test_unknown_zone_refuses(log: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_periods.main([str(log), "--tz", "Unknown/Nowhere", "--by", "hour"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("run periods refused:")


def test_missing_database_refuses_but_utc_works(
    log: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(name: str) -> None:
        raise ZoneInfoNotFoundError("time-zone database unavailable")

    monkeypatch.setattr(run_periods, "ZoneInfo", unavailable)
    assert run_periods.main([str(log), "--tz", "America/New_York", "--by", "day"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "run periods refused: 'time-zone database unavailable'\n"
    assert run_periods.main([str(log), "--tz", "UTC", "--by", "day"]) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("contents", [None, "not json\n", "{}\n"])
def test_bad_log_refuses(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    contents: str | None,
) -> None:
    path = tmp_path / "bad.jsonl"
    if contents is not None:
        path.write_text(contents)
    assert run_periods.main([str(path), "--tz", "UTC", "--by", "day"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("run periods refused:")


def test_seq_gap_refuses(log: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lines = log.read_text().splitlines()
    log.write_text(lines[0] + "\n" + lines[2] + "\n")
    assert run_periods.main([str(log), "--tz", "UTC", "--by", "hour"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "run periods refused: expected seq 2, got 3\n"


def test_unknown_figures_show_lower_bounds(log: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tick = read_jsonl(log)[1]
    assert tick.type == "usage.tick"
    tick = tick.model_copy(
        update={
            "seq": 1,
            "payload": tick.payload.model_copy(
                update={"model": "unpriced"},
            ),
        }
    )
    log.write_text(tick.model_dump_json() + "\n")
    assert run_periods.main([str(log), "--tz", "UTC", "--by", "hour"]) == 0
    captured = capsys.readouterr()
    assert (
        "at least 0.000000 agent-hours; at least 0.000000 active hours; parallelism —"
        in captured.out
    )
    assert "at least US$0.000000" in captured.out
    assert "1 unpriced, 0 ranged calls" in captured.out
    assert "activity missing_metrics; cost missing_metrics" in captured.out


def test_totals_use_global_fold_even_when_period_lower_bounds_differ(
    log: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    start = read_jsonl(log)[0]
    assert start.type == "agent.status"
    events = [
        start.model_copy(
            update={
                "seq": seq,
                "ts": f"2026-11-01T00:{minute:02d}:00.000Z",
                "payload": start.payload.model_copy(update={"state": state}),
            }
        )
        for seq, minute, state in [
            (1, 10, "working"),
            (2, 40, "paused"),
            (3, 20, "revising"),
            (4, 50, "done"),
        ]
    ]
    log.write_text("".join(e.model_dump_json() + "\n" for e in events))
    assert run_periods.main([str(log), "--tz", "UTC", "--by", "hour"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "at least 0.666667 agent-hours" in lines[1]
    assert lines[-1].startswith("Totals: at least 1.000000 agent-hours")


def test_empty_log_has_no_periods_and_zero_totals(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    assert run_periods.main([str(path), "--tz", "UTC", "--by", "day"]) == 0
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 2
    assert (
        "Totals: 0.000000 agent-hours; 0.000000 active hours; parallelism —; US$0.000000" in output
    )
    assert "at least" not in output


def test_zone_is_required(log: Path) -> None:
    with pytest.raises(SystemExit) as refusal:
        run_periods.main([str(log), "--by", "hour"])
    assert refusal.value.code == 2
