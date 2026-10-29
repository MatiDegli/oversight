"""Linked starts reject before effects and echo historical identity through their lifecycle."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from answering_helpers import origin, resolution
from live_helpers import (
    DEADLINE_S,
    GatedModel,
    LiveServer,
    Scripts,
    error_code,
    is_type,
    live_server,
    live_settings,
    two_fakes,
)
from revise_helpers import ready, request, revisable

from oversight.api import ApiError
from oversight.events import EventPayload, StoredEvent
from oversight_contracts.commands import AgentCreated, StartAgent
from oversight_contracts.events import AgentStatus, ChangeResolved

pytestmark = pytest.mark.anyio


def observe_admission(s: LiveServer, monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    admitted = asyncio.Event()
    check = s.live._check  # pyright: ignore[reportPrivateUsage]

    def observed(*args, **kwargs):
        result = check(*args, **kwargs)
        admitted.set()
        return result

    # No suspension between this check, reserving the slot and awaiting the held venv.
    monkeypatch.setattr(s.live, "_check", observed)
    return admitted


async def wait_for_signal(signal: asyncio.Event, description: str) -> None:
    try:
        async with asyncio.timeout(30):
            await signal.wait()
    except TimeoutError:
        pytest.fail(f"Timed out after 30 s waiting for {description}")


@asynccontextmanager
async def pending_start(
    s: LiveServer, request: StartAgent
) -> AsyncIterator[asyncio.Task[AgentCreated]]:
    pending = asyncio.create_task(s.live.start(request))
    try:
        yield pending
    finally:
        # Also join after a failed handshake/assertion, before releasing test-owned gates.
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize(
    "kind", ["review_card", "follow_main", "risky_area", "lead_plan", "examples_before_code"]
)
async def test_missing_producers_fail_before_client_or_worktree(tmp_path: Path, kind: str) -> None:
    clients = Scripts()
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path), clients) as s:
        ref = (await origin(s.store)).model_dump()
        ref["kind"] = kind
        if kind == "review_card":
            ref.update(node_id="m:f", question="Keep it?")
        if kind == "lead_plan":
            ref = dict(kind=kind, agent_id="agent-90", revision=1, evidence_seq=ref["evidence_seq"])
        response = await s.client.post("/agents", json=dict(task="t", answering=ref))
        assert error_code(response) == (409, "wrong_state")
        assert "producer unavailable" in response.text
        assert clients.calls == 0
        assert list((tmp_path / ".runtime" / "worktrees").glob("*")) == []
        assert (await s.client.post("/agents", json=dict(task="t"))).status_code == 201


async def test_reference_is_copied_and_echoed_through_revise_and_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clients = Scripts([revisable()])
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path), clients) as s:
        ref = await origin(s.store)
        expected = ref.model_dump(mode="json")
        body = StartAgent(task="t", answering=ref)
        created = await s.live.start(body)
        # Mutation of the submitted model must not mutate AgentTask or retained states.
        object.__setattr__(ref, "head_commit", "mutated")
        await ready(s)
        await s.store.publish(ChangeResolved.model_validate(resolution()))
        before_revision = s.store.last_seq
        assert (await request(s)).status_code == 202
        events = await ready(s, after=before_revision)
        proposal = next(e.payload for e in reversed(s.events()) if e.type == "change.proposed")
        attempts: list[str] = []
        publish = s.store.publish

        async def flaky(payload: EventPayload) -> StoredEvent:
            if isinstance(payload, AgentStatus) and payload.state == "done":
                attempts.append(payload.model_dump_json())
                if len(attempts) < 3:
                    raise OSError("temporary delivery failure")
            return await publish(payload)

        monkeypatch.setattr(s.store, "publish", flaky)
        before_reject = s.store.last_seq
        response = await s.client.post(
            f"/changes/{created.change_id}/reject",
            json=dict(revision=proposal.revision, head_commit=proposal.head_commit),
        )
        assert response.status_code == 202
        # A 202 only accepts the rejection. Wait until both done attempts failed and the
        # resolution freed the slot before testing that the next start drains delivery.
        await s.until(
            lambda e: (
                e.type == "error"
                and e.payload.change_id == created.change_id
                and not e.payload.fatal
                and e.payload.message.startswith(
                    f"The close-out of {created.change_id} is incomplete (done:"
                )
            ),
            after=before_reject,
        )
        assert len(attempts) == 2
        assert (await s.client.post("/agents", json=dict(task="t"))).status_code == 201
        await s.until(lambda e: e.type == "agent.status" and e.payload.state == "done")
        states = [
            e.payload
            for e in s.events()
            if e.type == "agent.status" and e.payload.agent_id == created.agent_id
        ]
        assert {p.state for p in states} >= {
            "starting",
            "working",
            "ready_for_review",
            "revising",
            "done",
        }
        assert all(p.model_dump(mode="json")["answering"] == expected for p in states)
        assert events
        assert len(attempts) == 3 and len(set(attempts)) == 1
        done = next(
            e
            for e in reversed(s.events())
            if e.type == "agent.status" and e.payload.state == "done"
        )
        async with s.client.stream(
            "GET", "/events", headers={"Last-Event-ID": str(done.seq - 1)}
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    assert json.loads(line[5:])["payload"]["answering"] == expected
                    break


async def test_race_after_admission_wait_rejects_without_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clients = Scripts()
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path), clients) as s:
        ref = await origin(s.store)
        with monkeypatch.context() as held:
            gate = asyncio.get_running_loop().create_future()
            held.setattr(s.live, "_venv", gate)
            admitted = observe_admission(s, held)
            async with pending_start(s, StartAgent(task="t", answering=ref)) as pending:
                await wait_for_signal(admitted, "admission before the venv wait")
                assert s.live._slots["A"] == "starting"  # pyright: ignore[reportPrivateUsage]
                await s.store.publish(ChangeResolved.model_validate(resolution()))
                gate.set_result(None)
                with pytest.raises(ApiError) as caught:
                    await pending
        assert caught.value.code == "wrong_state"
        assert clients.calls == 0
        assert list((tmp_path / ".runtime" / "worktrees").glob("*")) == []
        assert (await s.client.post("/agents", json=dict(task="t"))).status_code == 201


@pytest.mark.parametrize("reason", ["stopped", "ledger", "normal"])
async def test_halt_and_stop_keep_provenance(tmp_path: Path, reason: str) -> None:
    from oversight.agents import ScriptedModel, parse_script

    model = (
        ScriptedModel(parse_script('{"task":"t","turns":[{"text":"No tool"}]}'))
        if reason == "normal"
        else GatedModel()
    )
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda *_: model) as s,
    ):
        ref = await origin(s.store)
        created = await s.live.start(StartAgent(task="t", answering=ref))
        if isinstance(model, GatedModel):
            await model.entered.wait()
        if reason == "stopped":
            assert (await s.client.post(f"/agents/{created.agent_id}/stop")).status_code == 202
        elif reason == "ledger":
            # Inject the cancellation marker used by a failed ledger; inference stays fake.
            s.live._ledger_lost_agents.add(created.agent_id)  # pyright: ignore[reportPrivateUsage]
            s.live._runs[created.agent_id].cancel()  # pyright: ignore[reportPrivateUsage]
        state = "stopped" if reason == "stopped" else "halted"
        events = await s.until(is_type("agent.status", agent_id=created.agent_id, state=state))
        assert all(
            e.payload.answering == ref
            for e in events
            if e.type == "agent.status" and e.payload.agent_id == created.agent_id
        )


@pytest.mark.parametrize("failure", ["cancel", "client"])
async def test_failed_start_does_not_consume_origin_or_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    clients = Scripts()
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path), clients) as s:
        ref = await origin(s.store)
        if failure == "cancel":
            with monkeypatch.context() as held:
                gate = asyncio.get_running_loop().create_future()
                held.setattr(s.live, "_venv", gate)
                admitted = observe_admission(s, held)
                async with pending_start(s, StartAgent(task="t", answering=ref)) as pending:
                    await wait_for_signal(admitted, "admission before cancelling the start")
                    assert s.live._slots["A"] == "starting"  # pyright: ignore[reportPrivateUsage]
                    pending.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await pending
                    assert not gate.cancelled()
                    gate.set_result(None)
        else:

            def broken(*_: object) -> None:
                raise RuntimeError("client unavailable")

            # Restore the TaskClients owner before retry and shutdown: Scripts has no aclose.
            with monkeypatch.context() as failed:
                failed.setattr(s.live, "_client_factory", broken)
                with pytest.raises(ApiError) as caught:
                    await s.live.start(StartAgent(task="t", answering=ref))
            assert caught.value.code == "internal"
        assert list((tmp_path / ".runtime" / "worktrees").glob("*")) == []
        assert (
            await s.client.post("/agents", json=dict(task="t", answering=ref.model_dump()))
        ).status_code == 201
        assert clients.calls == 1
    assert s.live._runs == {}, "server shutdown left an agent task running"
    writer = s.store._writer  # pyright: ignore[reportPrivateUsage]
    assert writer is not None and writer.done(), "server shutdown left the event writer running"


async def test_lock_wait_is_revalidated_before_client_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clients = Scripts()
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path), clients) as s:
        ref = await origin(s.store)
        requested = asyncio.Event()
        locks = s.live.workspace.locks

        @asynccontextmanager
        async def observed_locks(**kwargs):
            if kwargs.get("main"):
                requested.set()
            async with locks(**kwargs):
                yield

        monkeypatch.setattr(s.live.workspace, "locks", observed_locks)
        async with pending_start(s, StartAgent(task="t", answering=ref)) as pending:
            # Acquiring this free lock does not yield: it is held before the start runs.
            async with locks(main=True):
                await wait_for_signal(requested, "the admitted start to request main's lock")
                assert not pending.done() and clients.calls == 0
                await s.store.publish(ChangeResolved.model_validate(resolution()))
            with pytest.raises(ApiError) as caught:
                await pending
        assert caught.value.code == "wrong_state"
        assert clients.calls == 0
        assert list((tmp_path / ".runtime" / "worktrees").glob("*")) == []
        assert (await s.client.post("/agents", json=dict(task="t"))).status_code == 201


async def test_same_origin_allows_multiple_tasks_and_status_divergence_is_rejected(
    tmp_path: Path,
) -> None:
    model = GatedModel()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), lambda *_: model) as s:
        ref = await origin(s.store)
        for slot in ("A", "B"):
            response = await s.client.post(
                "/agents", json=dict(task="t", slot=slot, answering=ref.model_dump())
            )
            assert response.status_code == 201
        await s.until(is_type("agent.status", agent_id="agent-2", state="working"))
        statuses = [
            e.payload
            for e in s.events()
            if e.type == "agent.status" and e.payload.agent_id in {"agent-1", "agent-2"}
        ]
        assert all(p.answering == ref for p in statuses)
        before = s.store.last_seq
        with pytest.raises(ValueError, match="answering"):
            await s.live._publish(statuses[-1].model_copy(update={"answering": None}))  # pyright: ignore[reportPrivateUsage]
        assert s.store.last_seq == before


@pytest.mark.parametrize("attachment", [{}, dict(answering=None)])
async def test_unlinked_starts_keep_null_answering(
    tmp_path: Path, attachment: dict[str, object]
) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        assert (
            await s.client.post("/agents", json=dict(task="t", **attachment))
        ).status_code == 201
        await ready(s)
        assert all(e.payload.answering is None for e in s.events() if e.type == "agent.status")
