"""The cost CLI labels known subtotals, source dates and coverage explicitly."""

import dataclasses
import json
from pathlib import Path

import pytest

import run_cost
from oversight.events import price_usage, read_jsonl

DEMO = Path(__file__).resolve().parents[2] / "fixtures/events/recorded-demo.jsonl"


def test_demo_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cost.main([str(DEMO)]) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "17 calls, 7 priced: at least US$0.000978 list-price equivalent (10 unpriced); "
        "per accepted change at least US$0.000489 over 2 accepted; "
        "list prices retrieved 2026-10-30 from https://dev.meta.ai/docs/pricing-rate-limits; "
        "through seq 69; coverage missing_metrics\n"
    )
    assert captured.err == ""


def test_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cost.main([str(DEMO), "--json"]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["coverage"] == "missing_metrics"
    assert result["unpriced_calls"] == 10
    assert (
        captured.out
        == json.dumps(dataclasses.asdict(price_usage(read_jsonl(DEMO))), sort_keys=True) + "\n"
    )
    assert captured.err == ""


def test_no_accepted_changes_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "empty.jsonl"
    log.write_text("")
    assert run_cost.main([str(log)]) == 0
    line = capsys.readouterr().out
    assert line.startswith("0 calls, 0 priced: US$0.000000 list-price equivalent; ")
    assert "per accepted change unavailable (no accepted changes);" in line
    assert line.endswith("through seq 0; coverage complete\n")


def test_refuses_a_bad_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "gap.jsonl"
    events = read_jsonl(DEMO)
    log.write_text(events[0].model_dump_json() + "\n" + events[2].model_dump_json() + "\n")
    assert run_cost.main([str(log)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("run cost refused: expected seq 2, got 3")


@pytest.mark.parametrize("contents", [None, "not json\n", "{}\n"])
def test_refuses_missing_or_invalid_log(
    contents: str | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "invalid.jsonl"
    if contents is not None:
        log.write_text(contents)
    assert run_cost.main([str(log)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("run cost refused:")


@pytest.mark.parametrize(
    "counts,expected",
    [
        ((1_000_000, 400_000, 1_000_000), "US$0.260800"),
        ((1_000_000, None, 0), "at least US$0.002000"),
        ((1, 2, 0), "at least US$0.000000"),
    ],
)
def test_exact_ranged_and_incomplete_lines(
    counts: tuple[int, int | None, int],
    expected: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events = read_jsonl(DEMO)
    tick = next(
        event
        for event in events
        if event.type == "usage.tick" and event.payload.model == "muse-spark-1.3-contributor"
    )
    tick = tick.model_copy(
        update={
            "seq": 1,
            "payload": tick.payload.model_copy(
                update={
                    "input_tokens": counts[0],
                    "cached_tokens": counts[1],
                    "output_tokens": counts[2],
                }
            ),
        }
    )
    resolution = next(event for event in events if event.type == "change.resolved")
    log = tmp_path / "call.jsonl"
    log.write_text(
        tick.model_dump_json()
        + "\n"
        + resolution.model_copy(update={"seq": 2}).model_dump_json()
        + "\n"
    )
    assert run_cost.main([str(log)]) == 0
    line = capsys.readouterr().out
    assert f"1 calls, 1 priced: {expected} list-price equivalent" in line
    assert f"per accepted change {expected} over 1 accepted;" in line
    if counts[1] is None:
        assert "(1 with unreported counts)" in line
    elif counts[1] > counts[0]:
        assert line.endswith("coverage incomplete: usage at seq 1 is inconsistent\n")
