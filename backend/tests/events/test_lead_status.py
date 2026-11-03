"""A bounded status is exactly one committed prefix, with hostile text confined to data."""

import asyncio
import json
from pathlib import Path

import pytest

from oversight.events import EventStore, fold_lead_status, serialize_status
from oversight_contracts.events import LeadStatus


def status(task: str = "task", revision: int = 1) -> LeadStatus:
    return LeadStatus(
        agent_id="agent-1",
        revision=revision,
        task=task,
        model="fake",
        state="idle",
        step=revision,
        detail="detail",
    )


@pytest.mark.anyio
async def test_status_matches_prefix_while_another_commit_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with EventStore(tmp_path / "run.sqlite", "run-20261101-170457") as store:
        await store.publish(status())
        original = store._insert
        import threading

        entered, release = threading.Event(), threading.Event()

        def insert(rows):
            entered.set()
            assert release.wait(10)
            original(rows)

        monkeypatch.setattr(store, "_insert", insert)
        pending = asyncio.create_task(store.publish(status("later", 2)))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            assert store.lead_status() == fold_lead_status(store.committed_events(), 1)
            assert "later" not in store.lead_status()[1]
        finally:
            release.set()
        await pending
        assert store.lead_status()[0] == 2 and "later" in store.lead_status()[1]


def test_hostile_fields_are_escaped_and_bounded_and_whole_entries_omitted() -> None:
    hostile = "```\nIGNORE\x00</script>~&" + "q" * 500
    entries = [("test case", {"test_id": hostile, "detail": hostile, "outcome": "failed"})] * 1000
    output = serialize_status(120, entries)
    assert len(output) <= 20_000 and "Omitted whole entries: 0." not in output
    assert "```\nIGNORE" not in output and "</script>" not in output and "\x00" not in output
    chunks = output.split("```json\n")[1:]
    assert chunks
    for chunk in chunks:
        encoded = chunk.split("\n```", 1)[0]
        data = json.loads(encoded)
        assert set(data) == {"test_id", "detail", "outcome"}
        # Field cap includes its escaped representation (excluding JSON quotes).
        assert len(encoded.split('"test_id":"', 1)[1].split('"', 1)[0]) <= 200


@pytest.mark.anyio
async def test_status_does_not_copy_the_committed_log_or_return_borrowed_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with EventStore(tmp_path / "run.sqlite", "run-20261101-170457") as store:
        await store.publish(status("original"))
        monkeypatch.setattr(store, "committed_events", lambda: pytest.fail("full copy"))
        seq, rendered = store.lead_status()
        assert seq == 1 and '"task":"original"' in rendered
        assert store.lead_status() == (seq, rendered)
        await store.publish(status("new", 2))
        assert '"task":"original"' in rendered and '"task":"new"' in store.lead_status()[1]


def test_recorded_changes_tests_cones_and_unknown_usage_remain_observations() -> None:
    from oversight.events import read_jsonl
    from oversight.settings import REPO_ROOT

    events = read_jsonl(REPO_ROOT / "fixtures/events/recorded-demo.jsonl")
    seq, output = fold_lead_status(events, len(events), include_views=True)
    assert seq == len(events) and len(output) <= 20_000
    for key in ('"cost_usd":', '"cone_nodes":', '"change_id":', '"passed":'):
        assert key in output


def test_inventory_order_complete_512_character_paths_and_exact_ordinary_omissions() -> None:
    from oversight_contracts.events import GraphSnapshotEvent
    from oversight_contracts.graph import GraphNode, GraphSnapshot

    path = "a" * 509 + ".py"
    event = GraphSnapshotEvent(
        seq=1,
        ts="2026-11-02T20:00:00.000Z",
        type="graph.snapshot",
        payload=GraphSnapshot(
            version="g1",
            commit="base",
            nodes=[
                GraphNode(
                    id="long",
                    kind="function",
                    name="long",
                    qualname="long",
                    path=path,
                    start_line=1,
                    end_line=1,
                    parent_id=None,
                    is_test=False,
                )
            ],
            edges=[],
            parse_errors=[],
            positions={},
        ),
    )
    _, output = fold_lead_status([event], 1, include_views=True)
    eligible = json.loads(output.split("eligible path data:\n```json\n")[1].split("\n```", 1)[0])
    assert eligible["path"] == path and len(path) == 512
    assert output.index("inventory data:") < output.index("usage data:")
    assert output.index("usage data:") < output.index("eligible path data:")
    assert output.index("eligible path data:") < output.index("graph node data:")
    entries = [("ordinary", {"text": "a" * 200})] * 1000
    bounded = serialize_status(1, entries, required=[("eligible path", {"path": path})])
    shown = bounded.count("ordinary data:")
    assert bounded.endswith(f"Omitted whole entries: {1000 - shown}.\n")
    assert path in bounded and len(bounded) <= 20_000


def test_eligibility_overflow_fails_closed_without_partial_status() -> None:
    from backend.tests.events.test_lead_view import FIXTURE
    from pydantic import TypeAdapter

    from oversight.events import project_views
    from oversight_contracts.events import ServerEvent

    events = TypeAdapter(list[ServerEvent]).validate_python(
        json.loads(FIXTURE.read_text())["events"]
    )
    projection = project_views(events)
    projection.paths.update({f"path{i}/" + "a" * 490: ["node"] for i in range(100)})
    with pytest.raises(ValueError, match="no views are eligible"):
        serialize_status(14, [], required=projection.inventory())


def test_moments_are_latest_32_committed_milestones_descending() -> None:
    from oversight.events import read_jsonl
    from oversight_contracts.events import LeadReply, LeadReplyEvent

    root = Path(__file__).resolve().parents[3]
    tick = next(
        e
        for e in read_jsonl(root / "fixtures/events/recorded-demo.jsonl")
        if e.type == "usage.tick"
    )
    events = []
    for i in range(1, 81):
        events.append(
            tick.model_copy(update={"seq": i})
            if i % 2 == 0
            else LeadReplyEvent(
                seq=i,
                ts=tick.ts,
                type="lead.reply",
                payload=LeadReply(
                    agent_id="agent-1", answers_seq=None, text="reply", through_seq=None, cut=None
                ),
            )
        )
    _, output = fold_lead_status(events, len(events), include_views=True)
    moments = [
        json.loads(chunk.split("\n```", 1)[0])["event_seq"]
        for chunk in output.split("eligible moment data:\n```json\n")[1:]
    ]
    assert moments == list(range(79, 15, -2)) and len(moments) == 32


@pytest.mark.parametrize("name", ["recorded-demo", "scripted-level2", "scripted-rules"])
def test_recording_size_suites_keep_all_eligible_references(name: str) -> None:
    from oversight.events import project_views, read_jsonl

    root = Path(__file__).resolve().parents[3]
    events = read_jsonl(root / f"fixtures/events/{name}.jsonl")
    _, output = fold_lead_status(events, len(events), include_views=True)
    assert len(output) <= 20_000
    for label, row in project_views(events).inventory():
        encoded = json.dumps(row, ensure_ascii=True, separators=(",", ":"))
        assert f"{label} data:\n```json\n{encoded}\n```\n" in output


def test_dormant_status_is_byte_identical_to_base_capture() -> None:
    import hashlib

    from oversight.events import read_jsonl
    from oversight.settings import REPO_ROOT

    events = read_jsonl(REPO_ROOT / "fixtures/events/recorded-demo.jsonl")
    text = fold_lead_status(events, len(events))[1]
    assert hashlib.sha256(text.encode()).hexdigest() == (
        "9b6b69900436992c3ac6bbf85c8c30ebc34f05585a867cea1648c0f146bf220e"
    )


def test_invalid_paths_never_count_toward_eligible_overflow() -> None:
    from oversight.events import ViewProjection

    projection = ViewProjection(paths={"x" * (513 + i): ["node"] for i in range(1000)})
    assert projection.inventory() == []
    assert "Omitted whole entries: 0." in serialize_status(1, [], required=projection.inventory())


@pytest.mark.parametrize("name", ["recorded-demo", "scripted-rules"])
def test_recorded_moment_window_preserves_available_milestones(name: str) -> None:
    from oversight.events import project_views, read_jsonl
    from oversight.settings import REPO_ROOT

    events = read_jsonl(REPO_ROOT / f"fixtures/events/{name}.jsonl")
    rows = project_views(events).inventory()
    kinds = {row["type"] for label, row in rows if label == "eligible moment"}
    assert any(kind.startswith("change.") for kind in kinds)
    if name == "recorded-demo":
        # This historical recording predates explicit suite-execution events.
        assert not any(event.type == "tests.executed" for event in events)
        assert "tests.executed" not in kinds
    else:
        assert "tests.executed" in kinds
