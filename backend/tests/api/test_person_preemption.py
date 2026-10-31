"""Live HTTP admission with real scheduling, Git, store and scripted models only."""

import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from decision_helpers import observed_body
from live_helpers import DEADLINE_S, error_code, is_type, live_server, two_fakes

from oversight.agents import ScriptedModel, parse_script
from oversight.app import TEMPLATE
from oversight.changes import ModeController, _recovery, _transaction
from oversight.changes._mode import StartCapability
from oversight_contracts.commands import SetMergeMode

pytestmark = pytest.mark.anyio
COMMENT = "Keep this exact request."
T1 = "change-1: rules authorization has started; wait for its result; if merged, use Revert"
T2 = "change-1 was merged by rules; use Revert to undo it"


def author_factory(run):
    script = json.dumps(
        {
            "task": "Edit notes",
            "turns": [
                {"tool_calls": [{"name": name, "arguments": args}]}
                for name, args in [
                    ("read_file", {"path": "notes.txt"}),
                    ("edit_file", {"path": "notes.txt", "old_text": "before", "new_text": "after"}),
                    ("run_tests", {}),
                    ("finish", {"summary": "Updated notes"}),
                ]
            ],
        }
    )

    class Author(ScriptedModel):
        async def complete(self, messages, tools):
            if not self._turns:
                assert run.recovered.is_set(), "Author resumed before verified recovery"
                run.comments.append(messages[-1]["content"])
                run.resumed.set()
                await asyncio.Event().wait()
            return await super().complete(messages, tools)

    return lambda profile, slot: Author(parse_script(script, profile), profile)


async def arm_rules(run, monkeypatch, authorization):
    server, ctx = run.server, run.manager._ctx
    ctx.mode = ModeController(availability=lambda: StartCapability(True, "Test only"))
    await server.live.set_merge_mode(SetMergeMode(mode="merge_when_safe", revision=0))
    isolation = server.live.workspace._isolation
    monkeypatch.setattr(isolation, "_born", True)
    monkeypatch.setattr(isolation, "_checked", True)
    monkeypatch.setattr(isolation, "select", lambda p, i, runner: runner)
    suite, ready, abort = (
        server.live.workspace.run_tests,
        _transaction.Transaction.ready,
        _recovery.abort_rules,
    )

    async def tests(*args, **kwargs):
        if kwargs.get("isolation") == "required" and not authorization:
            run.entered.set()
            await run.release.wait()
        return await suite(*args, **kwargs)

    async def boundary(tx):
        if (
            authorization
            and tx.reservation.wait.rec.rules_phase == "authorizing"
            and not run.entered.is_set()
        ):
            run.entered.set()
            await run.release.wait()
        await ready(tx)

    async def restored(*args, **kwargs):
        run.abort_entered.set()
        if run.hold_abort:
            await run.abort_release.wait()
        await abort(*args, **kwargs)
        run.recovered.set()

    monkeypatch.setattr(server.live.workspace, "run_tests", tests)
    monkeypatch.setattr(_transaction.Transaction, "ready", boundary)
    monkeypatch.setattr(_recovery, "abort_rules", restored)


@asynccontextmanager
async def rules_server(tmp_path, monkeypatch, *, authorization=False):
    template = tmp_path / "template"
    shutil.copytree(TEMPLATE, template)
    (template / "notes.txt").write_text("before\n")
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"task": "Review", "turns": [{"text": "VERDICT: accept"}]}))
    settings = two_fakes(
        tmp_path,
        cross_review=True,
        review_model="fake-b",
        reviewer_fake_b_script=review,
        budget_usd=1,
    )
    run = SimpleNamespace(
        entered=asyncio.Event(),
        release=asyncio.Event(),
        recovered=asyncio.Event(),
        abort_entered=asyncio.Event(),
        abort_release=asyncio.Event(),
        resumed=asyncio.Event(),
        hold_abort=False,
        comments=[],
    )
    # Neither API wiring nor runtime review is changed: use the explicit receipt port.
    monkeypatch.setattr("oversight.app.TEMPLATE", template)
    async with asyncio.timeout(DEADLINE_S), live_server(settings, author_factory(run)) as server:
        run.server, run.manager = server, server.live._manager
        assert run.manager is not None
        await arm_rules(run, monkeypatch, authorization)
        try:
            response = await server.client.post("/agents", json={"task": "Edit notes", "slot": "A"})
            assert response.status_code == 201
            reviewed = (await server.until(is_type("change.reviewed")))[-1]
            await asyncio.gather(
                *list(server.live._runs.values()), *list(server.live._reviews.values())
            )
            receipt = next(e for e in server.store._events if e.seq == reviewed.seq)
            run.manager.rules_received(receipt)
            await run.entered.wait()
            latest = run.manager._ctx.assessment.waits["change-1"].latest
            assert latest is not None
            assert all(r.result == "pass" for r in latest.rules if r.rule != "combined_tests")
            yield run
        finally:
            run.release.set()
            run.abort_release.set()


async def test_reject_during_rules_suite_answers_202_then_resolves(tmp_path, monkeypatch):
    async with rules_server(tmp_path, monkeypatch) as run:
        run.hold_abort = True
        s = run.server
        response = await s.client.post("/changes/change-1/reject", json=observed_body(s))
        assert response.status_code == 202 and response.content == b""
        await run.abort_entered.wait()
        assert not run.recovered.is_set()
        assert not any(e.type == "change.resolved" for e in s.events())
        run.abort_release.set()
        resolved = (await s.until(is_type("change.resolved")))[-1].payload
        assert run.recovered.is_set()
        assert resolved.decision == "rejected" and resolved.decided_by == "person"
        assert not any(e.type == "error" for e in s.events())


@pytest.mark.parametrize("shutdown", [False, True])
async def test_request_changes_during_rules_suite_resumes_author_after_recovery(
    tmp_path, monkeypatch, shutdown
):
    async with rules_server(tmp_path, monkeypatch) as run:
        run.hold_abort = True
        s = run.server
        response = await s.client.post(
            "/changes/change-1/request-changes", json={**observed_body(s), "comment": COMMENT}
        )
        assert response.status_code == 202 and response.content == b""
        await run.abort_entered.wait()
        assert not run.resumed.is_set()
        if shutdown:
            # Exercise the real _revise cancellation used by shutdown, before manager drain.
            revision = s.live._runs["agent-1"]
            await s.live._stop_runs()
            assert revision.cancelled()
            drain = asyncio.create_task(run.manager.aclose())
            run.abort_release.set()
            await drain
            [resolved] = [e.payload for e in s.events() if e.type == "change.resolved"]
            assert not run.resumed.is_set() and not run.comments
        else:
            run.abort_release.set()
            resolved = (await s.until(is_type("change.resolved")))[-1].payload
            await run.resumed.wait()
            assert len(run.comments) == 1 and COMMENT in run.comments[0]
        assert resolved.decision == "changes_requested" and resolved.comment == COMMENT
        assert not any(e.type == "error" for e in s.events())


async def test_decision_during_rules_authorization_answers_409(tmp_path, monkeypatch):
    async with rules_server(tmp_path, monkeypatch, authorization=True) as run:
        s = run.server
        body = {**observed_body(s), "comment": COMMENT}
        task = run.manager._agents["agent-1"].rules_task
        for decision in ("reject", "request-changes"):
            response = await s.client.post(f"/changes/change-1/{decision}", json=body)
            assert error_code(response) == (409, "wrong_state")
            assert response.json()["error"]["message"] == T1
        assert task.cancelling() == 0
        run.release.set()
        resolved = (await s.until(is_type("change.resolved")))[-1].payload
        await task
        assert resolved.decided_by == "rules" and resolved.merge == "merged"
        for decision in ("reject", "request-changes"):
            response = await s.client.post(f"/changes/change-1/{decision}", json=body)
            assert error_code(response) == (409, "wrong_state")
            assert response.json()["error"]["message"] == T2
