"""The closing line and JSON retain exact figures and report unavailable totals."""

import json
from pathlib import Path

import pytest

import run_hours
from oversight.events import read_jsonl
from oversight_contracts.events import ChangeReviewedEvent, LeadStatus, LeadStatusEvent
from oversight_contracts.review import CrossReview

DEMO = Path(__file__).resolve().parents[2] / "fixtures/events/recorded-demo.jsonl"
DEMO_LINE = (
    "2 agents, 0 reviews: 0.01 agent-hours (0:00:32) over 0.01 active hours (0:00:21); "
    "parallelism 1.54; through seq 69; coverage complete\n"
)


def test_demo_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_hours.main([str(DEMO)]) == 0
    captured = capsys.readouterr()
    assert captured.out == DEMO_LINE and captured.err == ""


def test_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_hours.main([str(DEMO), "--json"]) == 0
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["agent_ms"] == 32982 and output["coverage"] == "complete"
    assert output["active_ms"] == 21457 and output["groups_add_up"] is True
    assert len(captured.out.splitlines()) == 1 and captured.err == ""


def test_missing_metrics_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    event = ChangeReviewedEvent(
        seq=1,
        ts="2026-10-29T12:00:00.000Z",
        type="change.reviewed",
        payload=CrossReview(
            change_id="change-1",
            revision=1,
            base_commit="base",
            head_commit="head",
            main_commit="main",
            candidate_tree="tree",
            author_model="author-model",
            reviewer_model="review-model",
            reviewer_provider="fake",
            reasoning=None,
            verdict="clean",
            findings=[],
            complete=True,
            problems=[],
        ),
    )
    path = tmp_path / "review.jsonl"
    path.write_text(event.model_dump_json() + "\n")
    assert run_hours.main([str(path)]) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "0 agents, 1 review: at least 0.00 agent-hours (0:00:00) over at least 0.00 active hours "
        "(0:00:00); parallelism unavailable; through seq 1; coverage missing_metrics: "
        "review at seq 1 has no ready_for_review start\n"
    )
    assert captured.err == ""


def test_refuses_a_bad_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lines = DEMO.read_text().splitlines()
    path = tmp_path / "gap.jsonl"
    path.write_text(lines[0] + "\n" + lines[2] + "\n")
    assert run_hours.main([str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("run hours refused:")
    assert "expected seq 2, got 3" in captured.err


@pytest.mark.parametrize("content", [None, "{not json}\n"])
def test_refuses_missing_or_invalid_log(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    content: str | None,
) -> None:
    path = tmp_path / "invalid.jsonl"
    if content is not None:
        path.write_text(content)
    assert run_hours.main([str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("run hours refused:")


def test_empty_log_has_unavailable_parallelism(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    assert run_hours.main([str(path)]) == 0
    assert capsys.readouterr().out == (
        "0 agents, 0 reviews: 0.00 agent-hours (0:00:00) over 0.00 active hours (0:00:00); "
        "parallelism unavailable; through seq 0; coverage complete\n"
    )


def test_long_clock(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    events = read_jsonl(DEMO)
    start = next(event for event in events if event.type == "agent.status")
    end = start.model_copy(
        update={
            "seq": 2,
            "ts": "2026-10-27T20:54:19.041Z",
            "payload": start.payload.model_copy(update={"state": "done"}),
        }
    )
    start = start.model_copy(update={"seq": 1})
    path = tmp_path / "long.jsonl"
    path.write_text(start.model_dump_json() + "\n" + end.model_dump_json() + "\n")
    assert run_hours.main([str(path)]) == 0
    assert capsys.readouterr().out.startswith(
        "1 agent, 0 reviews: 2.03 agent-hours (2:02:03) over 2.03 active hours (2:02:03); "
    )


@pytest.mark.parametrize("count", [1, 2])
def test_lead_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str], count: int) -> None:
    events = [
        LeadStatusEvent(
            seq=n,
            ts="2026-10-29T12:00:00.000Z",
            type="lead.status",
            payload=LeadStatus(
                agent_id=f"agent-{n}",
                revision=1,
                task="Coordinate",
                model="lead-model",
                state="working",
                step=0,
                detail=None,
            ),
        )
        for n in range(1, count + 1)
    ]
    path = tmp_path / "leads.jsonl"
    path.write_text("".join(event.model_dump_json() + "\n" for event in events))
    assert run_hours.main([str(path)]) == 0
    assert capsys.readouterr().out.startswith(
        f"0 agents, 0 reviews, {count} lead{'' if count == 1 else 's'}: at least "
    )
