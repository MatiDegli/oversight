"""Three clean fake starts through production review transport, export and replay."""

import asyncio
import json
from pathlib import Path

import pytest
from live_helpers import DEADLINE_S, two_fakes
from revise_helpers import revisable

from oversight.agents import ModelClient, ScriptedModel, parse_script
from oversight.app import LiveRun
from oversight.events import (
    EventStore,
    NeedsYouWait,
    parse_needs_you,
    play,
    read_jsonl,
    write_jsonl,
)
from oversight_contracts.events import ErrorReport, ModelProfile

from .test_cross_review_lifecycle import events, start, until

pytestmark = pytest.mark.anyio


async def test_three_clean_fake_starts_request_once_and_preserve_second_review(
    tmp_path: Path,
) -> None:
    async with asyncio.timeout(DEADLINE_S):
        for number in range(3):
            root = tmp_path / str(number)
            root.mkdir()
            script = root / "review.json"
            script.write_text(
                json.dumps(
                    {
                        "task": "review",
                        "turns": [
                            {
                                "text": (
                                    "SHOULD-FIX shop/pricing.py:2: Shorten the docstring.\n"
                                    "VERDICT: request changes"
                                )
                            }
                        ],
                    }
                )
            )
            settings = two_fakes(
                root,
                agent_model_b="fake-b",
                cross_review=True,
                reviewer_fake_b_script=script,
            )

            def factory(profile: ModelProfile, slot: str) -> ModelClient:
                return ScriptedModel(parse_script(revisable(), profile), profile)

            live = LiveRun(settings, factory)
            await live.startup()
            try:
                await start(live)
                terminal = await until(live, "error")
                assert isinstance(terminal.payload, ErrorReport)
                note = parse_needs_you(terminal.payload.message)
                assert note and note.code == "round_exhausted" and note.revision == 2
                await asyncio.gather(*list(live._reviews.values()), *list(live._runs.values()))
                es = events(live)
                reviews = [e for e in es if e.type == "change.reviewed"]
                rounds = [e for e in es if e.type == "change.resolved"]
                assert [e.payload.revision for e in reviews] == [1, 2]
                assert all(e.payload.verdict == "changes_requested" for e in reviews)
                assert len(rounds) == 1 and rounds[0].payload.review_seq == reviews[0].seq
                assert rounds[0].payload.decided_by == "cross_review"
                wait = NeedsYouWait("change-1", 2, note.head, number)
                assert wait.accepts(terminal.payload, generation=number)
                assert not wait.accepts(terminal.payload, generation=number + 1)
                foreign = terminal.payload.model_copy(
                    update={
                        "message": terminal.payload.message.replace(
                            "code=round_exhausted", "code=foreign"
                        )
                    }
                )
                assert not wait.accepts(foreign, generation=number)
                # Concurrent producers receive the same durable receipt, without another event.
                before = live.store.last_seq
                receipts = await asyncio.gather(
                    *[live.store.publish(terminal.payload) for _ in range(2)]
                )
                assert all(r.seq == terminal.seq for r in receipts)
                assert live.store.last_seq == before
            finally:
                await live.shutdown()
            recorded = root / "recorded.jsonl"
            count = write_jsonl(recorded, live.store._events)
            validated = read_jsonl(recorded)
            assert len(validated) == count
            async with EventStore(
                root / "replay.sqlite", live.run_id, mode="replay", allow_fake_provider=True
            ) as replay:
                await play(replay, validated, speed=0)
                out = root / "replayed.jsonl"
                write_jsonl(out, replay._events)
                assert out.read_bytes() == recorded.read_bytes()
