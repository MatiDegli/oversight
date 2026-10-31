"""The live cross-review path spends no round unless the repeat guard clears it."""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from live_helpers import DEADLINE_S, two_fakes

from oversight.agents import (
    ModelClient,
    ModelReply,
    ReviewInputs,
    ReviewOutcome,
    Script,
    ScriptedModel,
    parse_script,
    review_candidate,
)
from oversight.app import LiveRun
from oversight.events import parse_needs_you
from oversight_contracts.commands import StartAgent
from oversight_contracts.events import ModelProfile
from oversight_contracts.review import ChangeTarget

pytestmark = pytest.mark.anyio
PRICE = "compute_price(unit_price, quantity) for quantity in quantities"


class Reviewer:
    async def __call__(
        self,
        client: ModelClient,
        target: ChangeTarget,
        inputs: ReviewInputs,
        *,
        author: ModelProfile,
        author_reply_models: Sequence[str | None] = (),
    ) -> ReviewOutcome:
        assert inputs.diff and inputs.tests.outcome == "failed"
        text = "SHOULD-FIX shop/pricing.py:25: Correct the sample prices.\nVERDICT: request changes"
        return await review_candidate(
            ScriptedModel(
                Script("review", [ModelReply(text, [], model=client.profile.model)]), client.profile
            ),
            target,
            inputs,
            author=author,
            author_reply_models=author_reply_models,
        )


def script(*, reachable: bool) -> str:
    def call(name: str, **arguments: Any) -> dict[str, Any]:
        return {"tool_calls": [{"name": name, "arguments": arguments}]}

    turns = [
        call("read_file", path="shop/pricing.py", start_line=1, end_line=100),
        call(
            "edit_file",
            path="shop/pricing.py",
            old_text=PRICE,
            new_text=PRICE.replace(") for", ") + 1 for"),
        ),
        call("run_tests"),
    ]
    if reachable:
        turns.append(
            call(
                "edit_file",
                path="shop/pricing.py",
                old_text='"""Price for a few sample quantities, handy for a price list."""',
                new_text='"""Price for the sample quantities."""',
            )
        )
    turns += [
        call("finish", summary="Changed sample prices."),
        call("finish", summary="Kept sample prices."),
    ]
    return json.dumps({"task": "Change sample prices.", "turns": turns})


@asynccontextmanager
async def running(tmp_path: Path, *, reachable: bool) -> AsyncIterator[LiveRun]:
    review_script = tmp_path / "review.json"
    review_script.write_text(json.dumps({"task": "review", "turns": [{"text": "VERDICT: accept"}]}))
    settings = two_fakes(
        tmp_path,
        agent_model_b="fake",
        cross_review=True,
        review_model="fake-b",
        reviewer_fake_script=review_script,
        reviewer_fake_b_script=review_script,
    )

    def factory(profile, slot):
        return ScriptedModel(parse_script(script(reachable=reachable), profile), profile)

    live = LiveRun(settings, factory, reviewer=Reviewer())
    async with asyncio.timeout(DEADLINE_S):
        await live.startup()
        try:
            yield live
        finally:
            await live.shutdown()


async def until(live: LiveRun, kind: str) -> None:
    async for stored in live.store.subscribe(0):
        if stored.type == kind:
            return
    raise AssertionError("store ended before expected event")


async def settled(live: LiveRun) -> None:
    while live._runs or live._reviews:
        await asyncio.gather(*list(live._runs.values()), *list(live._reviews.values()))


async def test_unchanged_failure_asks_instead_of_revising(tmp_path: Path) -> None:
    async with running(tmp_path, reachable=False) as live:
        await live.start(StartAgent(task="Change sample prices.", slot="A"))
        await until(live, "change.proposed")
        await settled(live)
        es = live.store.committed_events()
        notes = [parse_needs_you(e.payload.message) for e in es if e.type == "error"]
        assert len(notes) == 1 and notes[0] is not None
        note = notes[0]
        assert note.code == "unchanged_failure" and note.revision == 1
        assert note.reason == (
            "The same failure has no edit reached by its test: "
            "tests/test_pricing.py::test_price_breaks_lists_sample_quantities"
        )
        assert not any(e.type == "change.resolved" for e in es)
        assert not any(e.type == "agent.status" and e.payload.state == "revising" for e in es)
        assert live._manager is not None
        assert not live._manager._agents["agent-1"].review_consumed
        assert [
            e.type
            for e in es
            if e.type
            in {
                "change.proposed",
                "change.facts_recorded",
                "change.reviewed",
                "error",
            }
        ] == ["change.proposed", "change.facts_recorded", "change.reviewed", "error"]


async def test_edit_reaching_the_failing_test_starts_the_round(tmp_path: Path) -> None:
    async with running(tmp_path, reachable=True) as live:
        await live.start(StartAgent(task="Change sample prices.", slot="A"))
        await until(live, "change.resolved")
        await settled(live)
        es = live.store.committed_events()
        resolved = [e for e in es if e.type == "change.resolved"]
        assert len(resolved) == 1 and resolved[0].payload.decided_by == "cross_review"
        assert any(e.type == "agent.status" and e.payload.state == "revising" for e in es)
        assert live._manager is not None and live._manager._agents["agent-1"].review_consumed
        assert [
            e.type
            for e in es
            if e.type
            in {
                "change.proposed",
                "change.facts_recorded",
                "change.reviewed",
                "change.resolved",
                "error",
            }
        ] == [
            "change.proposed",
            "change.facts_recorded",
            "change.reviewed",
            "change.resolved",
            "change.proposed",
            "change.facts_recorded",
            "change.reviewed",
            "error",
        ]
        notes = [parse_needs_you(e.payload.message) for e in es if e.type == "error"]
        assert len(notes) == 1 and notes[0] is not None and notes[0].code == "round_exhausted"
        runs = [
            e.payload.suite
            for e in es
            if e.type == "tests.executed" and e.payload.phase in {"agent", "proposal"}
        ]
        assert all(r.cases == runs[0].cases for r in runs)
