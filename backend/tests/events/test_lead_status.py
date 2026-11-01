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
    seq, output = fold_lead_status(events, len(events))
    assert seq == len(events) and len(output) <= 20_000
    for key in ('"cost_usd":', '"cone_nodes":', '"change_id":', '"passed":'):
        assert key in output
