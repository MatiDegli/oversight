"""The review port receives only confirmed, uninterrupted facts and explicit authorship."""

import asyncio
from typing import Any

import pytest
from changes_helpers import EDIT, Harness
from decision_helpers import observed_target

from oversight.changes import ReviewCapture, _proposal
from oversight_contracts.events import ChangeProposed
from oversight_contracts.review import ChangeFacts

pytestmark = pytest.mark.anyio


class Notices:
    def __init__(self, h: Harness) -> None:
        self.h = h
        self.received: list[tuple[ReviewCapture, str]] = []

    def facts_ready(self, capture: ReviewCapture, proposal: ChangeProposed, *, author: str) -> None:
        facts = self.h.payloads(ChangeFacts)[-1]
        assert capture.target.head_commit == proposal.head_commit == facts.head_commit
        assert capture.target.candidate_tree == facts.candidate_tree
        assert capture.target.revision == facts.revision
        self.received.append((capture, author))


async def test_agent_and_person_proposals_carry_authorship_after_receipt(h: Harness) -> None:
    notices = Notices(h)
    h.manager._ctx.ports.review = notices
    tree = await h.run_agent(1, EDIT)
    assert [author for _, author in notices.received] == ["agent"]
    read = await tree.read_text(EDIT[0])
    await h.manager.save_for_review("agent-1", EDIT[0], read.content + "# user\n", read.hash)
    assert [author for _, author in notices.received] == ["agent", "user"]


@pytest.mark.parametrize("phase", ["proposal", "probe", "facts"])
async def test_cancelled_partial_facts_never_notify(
    h: Harness, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    notices = Notices(h)
    h.manager._ctx.ports.review = notices
    await h.open(1)
    assert await h.edit(1, *EDIT)
    entered = asyncio.Event()
    if phase == "probe":

        async def probe(*args: Any, **kwargs: Any) -> Any:
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(_proposal, "probe_added_tests", probe)
    else:
        h.hold = ChangeProposed if phase == "proposal" else ChangeFacts
        entered = h.held
    task = asyncio.create_task(h.finish(1))
    await entered.wait()
    task.cancel()
    h.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert h.payloads(ChangeFacts) and not notices.received
    h.manager.run_ended("agent-1", None)
    assert h.manager._agents["agent-1"].state == "ready"


async def test_probe_exception_still_notifies_after_diagnostic_facts(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    notices = Notices(h)
    h.manager._ctx.ports.review = notices

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise OSError("probe unavailable")

    monkeypatch.setattr(_proposal, "probe_added_tests", broken)
    await h.run_agent(1, EDIT)
    assert len(notices.received) == 1
    assert "added tests: probes unavailable (OSError)" in h.payloads(ChangeFacts)[-1].problems
    await h.manager.request_changes("change-1", "Revise", **observed_target(h.manager, "change-1"))
