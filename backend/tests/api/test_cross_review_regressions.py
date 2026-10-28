"""Regression coverage for halted review cancellation and admission diagnostics."""

import asyncio
from pathlib import Path
from typing import Any, Literal

import pytest
from decision_helpers import observed_target

from oversight.agents import AgentRun, ModelAPIError
from oversight.api import ApiError
from oversight.events import parse_needs_you
from oversight_contracts.commands import ErrorCode

from .test_cross_review_lifecycle import Reviewer, events, running, start, until

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("action", ["stop", "reject"])
async def test_human_admission_cancels_halted_author_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    async def failed_loop(self: AgentRun, messages: Any) -> str:
        raise ModelAPIError("scripted failure")

    monkeypatch.setattr(AgentRun, "_loop", failed_loop)
    entered, release = asyncio.Event(), asyncio.Event()
    reviewer = Reviewer()
    async with running(tmp_path, reviewer) as live:
        real_report = live._report

        async def held_report(source: Any, message: str, **kwargs: Any) -> None:
            note = parse_needs_you(message)
            if note and note.code == "author_halted":
                entered.set()
                await release.wait()
            await real_report(source, message, **kwargs)

        monkeypatch.setattr(live, "_report", held_report)
        await start(live)
        await entered.wait()
        review = live._reviews["change-1"]
        run = live._kept["agent-1"]
        manager = live._manager
        assert manager is not None
        try:
            if action == "stop":
                live.stop("agent-1")
            else:
                manager.reject("change-1", None, **observed_target(manager, "change-1"))
            assert review.cancelling()  # admission must cancel before its task runs
            await until(live, "change.resolved")
        finally:
            release.set()
            await asyncio.gather(review, return_exceptions=True)
        assert review.cancelled() and not live._reviews
        assert not reviewer.calls and run.max_steps == 30
        assert not any(
            e.type == "error" and parse_needs_you(e.payload.message) for e in events(live)
        )


@pytest.mark.parametrize("stage", ["review", "revision"])
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("not_configured", "reviewer_credentials"),
        ("spend_brake", "spend_invalid"),
        ("wrong_state", None),
        ("internal", "review_api_failed"),
    ],
)
async def test_review_admission_api_errors_keep_their_meaning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: Literal["review", "revision"],
    code: ErrorCode,
    expected: str | None,
) -> None:
    reviewer = Reviewer(held=True)
    async with running(tmp_path, reviewer) as live:
        real_check = live._check_spending

        def failed_check(slot: Any, profile: Any) -> None:
            if stage == "review" and profile.id == "fake":
                real_check(slot, profile)  # the author can start normally
                return
            raise ApiError(code, "Scripted admission failure.")

        if stage == "review":
            monkeypatch.setattr(live, "_check_spending", failed_check)
        await start(live)
        if stage == "revision":
            await reviewer.at["change-1"].wait()
            monkeypatch.setattr(live, "_check_spending", failed_check)
            review = live._reviews["change-1"]
            reviewer.release["change-1"].set()
            await review
        else:
            await asyncio.gather(*list(live._runs.values()))
            await asyncio.gather(*list(live._reviews.values()))
        notes = [
            note
            for e in events(live)
            if e.type == "error" and (note := parse_needs_you(e.payload.message))
        ]
        assert [note.code for note in notes] == ([expected] if expected else [])
        assert all("restart" not in note.reason for note in notes)
        assert not live._runs and not live._decisions
        assert live._kept["agent-1"].max_steps == 30
        assert len(reviewer.calls) == (stage == "revision")
        assert live._manager is not None
        assert live._manager._agents["agent-1"].state == "ready"
        assert not any(e.type == "change.resolved" for e in events(live))
