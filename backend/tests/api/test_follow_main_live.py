"""A main save renews an idle proposal over the unchanged live HTTP/event contracts."""

import asyncio
import json
import shutil
from types import SimpleNamespace

import pytest
from live_helpers import DEADLINE_S, SERVER_EVENT, is_type, live_server, two_fakes

from oversight.app import TEMPLATE
from oversight.changes import ModeController
from oversight.changes._mode import StartCapability
from oversight_contracts.commands import SetMergeMode

from .test_person_preemption import author_factory

pytestmark = pytest.mark.anyio


async def test_save_on_main_rearms_idle_ready_change(tmp_path, monkeypatch):
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
    monkeypatch.setattr("oversight.app.TEMPLATE", template)
    run = SimpleNamespace(recovered=asyncio.Event())
    async with asyncio.timeout(DEADLINE_S), live_server(settings, author_factory(run)) as s:
        manager = s.live._manager
        assert manager is not None
        manager._ctx.mode = ModeController(availability=lambda: StartCapability(True, "Test"))
        await s.live.set_merge_mode(SetMergeMode(mode="merge_when_safe", revision=0))
        response = await s.client.post("/agents", json={"task": "Edit notes", "slot": "A"})
        assert response.status_code == 201
        await s.until(is_type("change.reviewed"))
        await asyncio.gather(*list(s.live._runs.values()), *list(s.live._reviews.values()))
        old = manager._ctx.assessment.waits["change-1"]
        assert old.task is not None
        await asyncio.gather(old.task)
        assert old.active and old.latest is not None and not old.rec.rules_consumed
        file = await s.client.get("/files", params={"path": "notes.txt"})
        assert file.status_code == 200
        body = file.json()
        response = await s.client.put(
            "/files",
            json={
                "path": "notes.txt",
                "content": "person saved\n",
                "base_hash": body["hash"],
            },
        )
        assert response.status_code == 200
        new = manager._ctx.assessment.waits["change-1"]
        assert new.task is not None
        await asyncio.gather(new.task)
        assert new is not old and new.deadline == old.deadline
        events = [SERVER_EVENT.validate_json(e.envelope) for e in s.store._events]
        delta = max(e.seq for e in events if e.type == "graph.delta")
        assessments = [e for e in events if e.type == "change.assessed" and e.seq > delta]
        assert len(assessments) == 1
        assert assessments[0].payload.main_commit == manager.snapshot.commit != old.main_commit
        assert not any(e.type == "error" for e in events)
