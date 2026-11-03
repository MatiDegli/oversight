"""Server-owned admission snapshots and publication races, without preview lifecycle calls."""

# Composition tests inspect owned locks and inject unavailable host infrastructure only.
# pyright: reportPrivateUsage=false

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from backend.tests.agents.lead_helpers import call, turn
from backend.tests.agents.test_lead_views import INVALID
from backend.tests.api.lead_helpers import ThreadModels, send, start_idle
from backend.tests.events.test_preview_resolution import EVENTS
from live_helpers import DEADLINE_S, live_server, two_fakes

from oversight.events import EventRejected, EventStore
from oversight.settings import REPO_ROOT
from oversight.workspace import PreviewProfile
from oversight_contracts.views import LeadView, PreviewView

pytestmark = pytest.mark.anyio
ARGUMENTS = {"kind": "preview", "agent_id": "agent-2", "caption": "Try this change"}
UNAVAILABLE = "Preview unavailable on this server. Call status again after availability changes."
SECRET = "http://private.invalid/app?token=planted-sensitive-output"


def profile() -> PreviewProfile:
    return PreviewProfile(
        adapter=REPO_ROOT / "backend/src/oversight/preview_adapter.py",
        interpreter=Path(sys.executable),
        ports=(19681,),
        origins=("http://127.0.0.1:8710",),
        trusted_demo=True,
    )


async def prepared(s, monkeypatch, state="ready_for_review"):
    await start_idle(s)
    await s.store.publish(
        EVENTS[2].payload.model_copy(update={"agent_id": "agent-2", "state": state})
    )
    await s.store.publish(EVENTS[5].payload.model_copy(update={"agent_id": "agent-2"}))
    manager = s.live.previews
    manager.profile = profile()
    # Namespaces are unavailable on some CI hosts; keep real profile and integrity admission.
    monkeypatch.setattr(
        type(manager.workspace), "isolation", property(lambda _: SimpleNamespace(available=True))
    )
    manager.set_status_sink(AsyncMock())
    spies = [AsyncMock() for _ in range(4)]
    for name, spy in zip(("start", "stop", "renew", "get"), spies, strict=True):
        monkeypatch.setattr(manager, name, spy)
    launch = Mock(side_effect=AssertionError("must never launch"))
    monkeypatch.setattr(manager, "process_factory", launch)
    lead = s.live.leads.leads["agent-1"]
    return lead, s.live.leads._tools(lead), [*spies, launch]


def untouched(s, spies) -> None:
    for spy in spies:
        spy.assert_not_called()
    assert not s.live.previews.has_owner
    assert not any(e.type == "preview.status" for e in s.events())
    assert s.live.previews.registry.read() is None


def lead_status(s):
    return next(e.payload for e in s.events() if e.type == "lead.status")


async def suggest(tools, arguments=None):
    return await tools.call("show_view", json.dumps(arguments or ARGUMENTS))


@pytest.mark.parametrize("state", ["ready_for_review", "halted"])
async def test_admitted_reference_only_event_with_distinct_lead_and_coding_agent(
    tmp_path, monkeypatch, state
):
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        _, tools, spies = await prepared(s, monkeypatch, state)
        assert s.live.previews.profile.configured
        assert not s.live.previews.profile.production_open
        status = await tools.call("status", "{}")
        assert (
            status.ok
            and 'eligible preview data:\n```json\n{"agent_id":"agent-2"}' in status.content
        )
        before = s.store.last_seq
        assert (await suggest(tools)).ok
        assert s.store.last_seq == before + 1
        event = s.events()[-1]
        assert event.type == "lead.view"
        assert event.payload.model_dump() == {
            "agent_id": "agent-1",
            "through_seq": before,
            "caption": "Try this change",
            "view": {"kind": "preview", "agent_id": "agent-2"},
        }
        untouched(s, spies)


def refuse(manager, fault, monkeypatch) -> None:
    profiles = {
        "missing": PreviewProfile(),
        "partial": replace(profile(), interpreter=None),
        "invalid": replace(profile(), ports=(8710,)),
        "own_source": replace(profile(), trusted_demo=False),
    }
    if fault in profiles:
        manager.profile = profiles[fault]
    elif fault == "isolation":
        monkeypatch.setattr(
            type(manager.workspace),
            "isolation",
            property(lambda _: SimpleNamespace(available=False)),
        )
    elif fault == "integrity":
        monkeypatch.setattr(manager.workspace.integrity, "main_suspect", True)
    elif fault == "sink":
        manager._sink = None
    elif fault == "resolver":
        manager.resolve_candidate = None
    elif fault == "memory":
        manager.memory_hold = lambda: SECRET
    elif fault == "closed":
        manager.close_admission()
    elif fault == "disabled":
        manager._disabled = True
    else:
        monkeypatch.setattr(manager, "_admit", Mock(side_effect=RuntimeError(SECRET)))


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "partial",
        "invalid",
        "own_source",
        "isolation",
        "integrity",
        "sink",
        "resolver",
        "memory",
        "closed",
        "disabled",
        "exception",
    ],
)
async def test_real_admission_refusal_omits_inventory_and_sanitizes(tmp_path, monkeypatch, fault):
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        _, tools, spies = await prepared(s, monkeypatch)
        with monkeypatch.context() as patch:
            refuse(s.live.previews, fault, patch)
            status = await tools.call("status", "{}")
            assert status.ok and "eligible preview data:" not in status.content
            before = s.store.committed_events()
            result = await suggest(tools)
            assert not result.ok and result.content == UNAVAILABLE
            assert SECRET not in status.content + result.content
            assert s.store.committed_events() == before
            untouched(s, spies)
        # Do not leave the injected manager state in normal server shutdown.
        s.live.previews._closed = s.live.previews._disabled = False
        s.live.previews.profile = profile()
        s.live.previews.memory_hold = lambda: None


@pytest.mark.parametrize("at_lock", [False, True])
@pytest.mark.parametrize("fault", ["disabled", "closed", "memory", "partial", "exception"])
async def test_lost_admission_after_status_or_while_waiting_for_lock(
    tmp_path, monkeypatch, at_lock, fault
):
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        lead, tools, spies = await prepared(s, monkeypatch)
        assert (await tools.call("status", "{}")).ok
        before = s.store.committed_events()
        pending = None
        if at_lock:
            await lead.lock.acquire()
            pending = asyncio.create_task(suggest(tools))
            await asyncio.sleep(0)
            assert not pending.done()
        with monkeypatch.context() as patch:
            refuse(s.live.previews, fault, patch)
            if at_lock:
                lead.lock.release()
                assert pending is not None
                outcome = await pending
            else:
                outcome = await suggest(tools)
            assert not outcome.ok and outcome.content == UNAVAILABLE
            assert s.store.committed_events() == before
            untouched(s, spies)
        s.live.previews._closed = s.live.previews._disabled = False
        s.live.previews.profile = profile()
        s.live.previews.memory_hold = lambda: None


async def test_gained_admission_requires_new_status_even_at_same_horizon(tmp_path, monkeypatch):
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        _, tools, spies = await prepared(s, monkeypatch)
        s.live.previews._disabled = True
        status = await tools.call("status", "{}")
        assert status.ok and "eligible preview data:" not in status.content
        before = s.store.committed_events()
        s.live.previews._disabled = False
        outcome = await suggest(tools)
        assert not outcome.ok and outcome.content == UNAVAILABLE
        assert s.store.committed_events() == before
        assert "eligible preview data:" in (await tools.call("status", "{}")).content
        assert (await suggest(tools)).ok
        assert sum(e.type == "lead.view" for e in s.events()) == 1
        untouched(s, spies)


@pytest.mark.parametrize(
    "fault", ["revision", "resolved", "revised", "noncoding", "wrong_run", "run_restart"]
)
async def test_stale_or_ineligible_target_never_publishes(tmp_path, monkeypatch, fault):
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        _, tools, spies = await prepared(s, monkeypatch)
        await tools.call("status", "{}")
        arguments = ARGUMENTS
        if fault == "revision":
            await s.store.publish(EVENTS[7].payload.model_copy(update={"agent_id": "agent-2"}))
        elif fault == "resolved":
            await s.store.publish(EVENTS[12].payload.model_copy(update={"decided_by": "person"}))
        elif fault == "revised":
            await s.store.publish(EVENTS[3].payload.model_copy(update={"agent_id": "agent-2"}))
        elif fault == "noncoding":
            arguments = ARGUMENTS | {"agent_id": "agent-1"}
        elif fault == "run_restart":
            await s.store.publish(next(e.payload for e in s.events() if e.type == "run.started"))
        else:
            with pytest.raises(EventRejected):
                await s.store.publish(
                    EVENTS[0].payload.model_copy(update={"run_id": "run-20261103-123456"})
                )
            # Prove that this reference is valid in the other run, but absent here.
            run_id = "run-20261103-123456"
            async with EventStore(tmp_path / "other.sqlite", run_id) as other:
                await other.publish(EVENTS[0].payload.model_copy(update={"run_id": run_id}))
                await other.publish(lead_status(s))
                await other.publish(
                    EVENTS[2].payload.model_copy(
                        update={
                            "agent_id": "agent-999",
                            "state": "ready_for_review",
                        }
                    )
                )
                await other.publish(EVENTS[5].payload.model_copy(update={"agent_id": "agent-999"}))
                view = LeadView(
                    agent_id="agent-1",
                    through_seq=other.last_seq,
                    caption=None,
                    view=PreviewView(kind="preview", agent_id="agent-999"),
                )
                assert other.view_resolver(other.last_seq)(view)
            arguments = ARGUMENTS | {"agent_id": "agent-999"}
        before = s.store.committed_events()
        assert not (await suggest(tools, arguments)).ok
        assert s.store.committed_events() == before
        untouched(s, spies)


@pytest.mark.parametrize("closing", ["lead", "service", "live"])
async def test_shutdown_while_waiting_for_lock_is_fixed_refusal(tmp_path, monkeypatch, closing):
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        lead, tools, spies = await prepared(s, monkeypatch)
        await tools.call("status", "{}")
        await lead.lock.acquire()
        pending = asyncio.create_task(suggest(tools))
        await asyncio.sleep(0)
        assert not pending.done()
        if closing == "lead":
            lead.stopping = True
        elif closing == "service":
            s.live.leads._closing = True
        else:
            s.live._closing = True
        before = s.store.committed_events()
        lead.lock.release()
        outcome = await pending
        assert not outcome.ok and outcome.content == UNAVAILABLE
        assert s.store.committed_events() == before
        untouched(s, spies)
        lead.stopping = s.live.leads._closing = s.live._closing = False


@pytest.mark.parametrize("arguments", [a for a in INVALID if a.get("kind") == "preview"])
async def test_sensitive_and_invalid_arguments_never_publish(tmp_path, monkeypatch, arguments):
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), ThreadModels()) as s:
        _, tools, spies = await prepared(s, monkeypatch)
        await tools.call("status", "{}")
        before = s.store.committed_events()
        outcome = await tools.call("show_view", json.dumps(arguments))
        assert (
            not outcome.ok and "secret" not in outcome.content and "invented" not in outcome.content
        )
        assert s.store.committed_events() == before
        untouched(s, spies)


async def test_scripted_status_preview_finish_and_fresh_next_turn(tmp_path, monkeypatch):
    models = ThreadModels()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), models) as s:
        _, _, spies = await prepared(s, monkeypatch)
        models.scripts.append(
            [
                turn(call("status")),
                turn(call("show_view", **(ARGUMENTS | {"url": SECRET}))),
                turn(call("show_view", **ARGUMENTS)),
                turn(
                    call(
                        "finish", reply="You can open the preview; review and Approve remain yours."
                    )
                ),
            ]
        )
        after = await send(s, "Can I try the app?")
        views = [e for e in s.events() if e.seq > after and e.type == "lead.view"]
        assert len(views) == 1 and views[0].payload.view.kind == "preview"
        assert SECRET not in "".join(e.model_dump_json() for e in s.events() if e.seq > after)
        models.scripts.append(
            [
                turn(call("show_view", **ARGUMENTS)),
                turn(call("finish", reply="Call status first.")),
            ]
        )
        after = await send(s, "Show it again")
        assert not any(e.type == "lead.view" for e in s.events() if e.seq > after)
        untouched(s, spies)
