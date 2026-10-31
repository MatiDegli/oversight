"""The supervision CLI quotes counts and unknowns without estimating missing spend."""

import ast
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

import supervision_summary
from oversight.events import SupervisionFold, fold_supervision, read_jsonl

DEMO = Path(__file__).resolve().parents[2] / "fixtures/events/recorded-demo.jsonl"


def test_project_imports_use_only_events_facade() -> None:
    source = Path(supervision_summary.__file__).read_text()
    imports = [
        module
        for node in ast.walk(ast.parse(source))
        for module in (
            [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else []
        )
        if module.startswith("oversight")
    ]
    assert imports == ["oversight.events"]


@pytest.mark.parametrize("missing", ["role", "model"])
def test_known_cost_with_missing_attribution_explains_unknown_total(
    missing: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = next(e for e in read_jsonl(DEMO) if e.type == "usage.tick")
    event = source.model_dump(mode="json")
    event["seq"] = 1
    event["payload"].update(change_id="change-1", role="agent", model="reported-model", cost_usd=2)
    event["payload"][missing] = None
    log = tmp_path / "missing-attribution.jsonl"
    log.write_text(json.dumps(event) + "\n")
    assert supervision_summary.main([str(log)]) == 0
    captured = capsys.readouterr()
    assert captured.out.count("cost unknown (role or model attribution missing)") == 2
    assert "0 calls with unknown cost" not in captured.out
    assert captured.err == ""


def test_recorded_demo_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert supervision_summary.main([str(DEMO)]) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "Run: 2 of 2 changes merged; 2 proposals; 0 review rounds; 0 open changes; "
        "resolutions: merged 2, conflict 0, changes_requested 0, rejected 0, stopped 0; "
        "lines proposed +37 -0; net lines merged 37; 19 steps, 773 ms; "
        "cost unknown (10 calls with unknown cost)\n"
        "agent-1: 1 of 1 changes merged; 1 proposals; 0 review rounds; 0 open changes; "
        "resolutions: merged 1, conflict 0, changes_requested 0, rejected 0, stopped 0; "
        "lines proposed +28 -0; net lines merged 28; 8 steps, 259 ms; cost US$0.002024\n"
        "agent-2: 1 of 1 changes merged; 1 proposals; 0 review rounds; 0 open changes; "
        "resolutions: merged 1, conflict 0, changes_requested 0, rejected 0, stopped 0; "
        "lines proposed +9 -0; net lines merged 9; 11 steps, 514 ms; "
        "cost unknown (10 calls with unknown cost)\n"
        "Spend that did not reach main: rejected: 0 changes, US$0.000000; "
        "stopped: 0 changes, US$0.000000; conflict: 0 changes, US$0.000000; "
        "earlier_rounds: 0 changes, US$0.000000\n"
        "Decisions: person 0, rules 0, cross_review 0, lead_agent 0, unrecorded 2; through seq 69\n"
    )
    assert captured.err == ""


def test_json_round_trips(capsys: pytest.CaptureFixture[str]) -> None:
    assert supervision_summary.main([str(DEMO), "--json"]) == 0
    captured = capsys.readouterr()
    expected = TypeAdapter(SupervisionFold).dump_json(fold_supervision(read_jsonl(DEMO)))
    assert json.loads(captured.out) == json.loads(expected)
    result = json.loads(captured.out)
    assert result["through_seq"] == 69 and result["run"]["net_lines_merged"] == 37
    assert result["agents"][1]["cost_usd"] is None
    assert result["not_merged"]["earlier_rounds"] == {
        "changes": 0,
        "cost_usd": 0,
        "unknown_calls": 0,
    }
    assert captured.err == ""


@pytest.mark.parametrize("contents", [None, "not json\n", "{}\n"])
def test_refuses_missing_or_malformed_log(
    contents: str | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "invalid.jsonl"
    if contents is not None:
        log.write_text(contents)
    assert supervision_summary.main([str(log)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("supervision summary refused: ")
    assert len(captured.err.splitlines()) == 1


def test_refuses_gap_in_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    events = read_jsonl(DEMO)
    log = tmp_path / "gap.jsonl"
    log.write_text(events[0].model_dump_json() + "\n" + events[2].model_dump_json() + "\n")
    assert supervision_summary.main([str(log)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "supervision summary refused: expected seq 2, got 3\n"


def test_empty_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "empty.jsonl"
    log.write_text("")
    assert supervision_summary.main([str(log)]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("Run: 0 of 0 changes merged; 0 proposals;")
    assert "cost US$0.000000\n" in captured.out
    assert captured.out.endswith("unrecorded 0; through seq 0\n")
    assert captured.err == ""
