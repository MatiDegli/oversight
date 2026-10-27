"""A shutdown while Request changes is deciding (R8): the decision is settled by the app, so no
change is left `deciding`, and none `working` with no run, when the manager drains and the store
closes. Each test holds the `changes_requested` publish before the store, or main's lock."""

import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

import pytest
from live_helpers import DEADLINE_S, BySlot, live_server, live_settings, start, two_fakes
from revise_helpers import HeldRequests, ready, revisable, revisable_b, scripted_a

from oversight.agents import AgentRun, ScriptedModel, parse_script

pytestmark = pytest.mark.anyio


class ProbeLock(asyncio.Lock):
    """Main's lock, with an event that is set when a call finds it held."""

    def __init__(self) -> None:
        super().__init__()
        self.contended = asyncio.Event()

    async def acquire(self) -> Literal[True]:
        if self.locked():
            self.contended.set()
        return await super().acquire()


class PopSignals(dict[str, Any]):
    """A `_decisions` that tells the test when an entry is popped, with no hook in the app."""

    def __init__(self) -> None:
        super().__init__()
        self.popped: defaultdict[str, asyncio.Event] = defaultdict(asyncio.Event)

    def pop(self, key: str, *default: Any) -> Any:  # pyright: ignore[reportIncompatibleMethodOverride]
        value = super().pop(key, *default)
        self.popped[key].set()
        return value


def state_of(s: Any, agent: str = "agent-1") -> str:
    return s.live._manager._agents[agent].state


def resolutions(s: Any) -> list[tuple[str, str]]:
    return [
        (e.payload.change_id, e.payload.decision) for e in s.events() if e.type == "change.resolved"
    ]


async def test_shutdown_during_revise_finishes_the_request_with_the_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `_revise` is cancelled as `_stop_runs` cancels it, while the store has not yet taken the
    # request: the request and `working` go together, and the run that never starts is ended.
    model = scripted_a()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
        HeldRequests(s.store, monkeypatch) as held,
    ):
        live = s.live
        await start(s)
        await ready(s)
        live.request_changes("change-1", "Shorter, please.")
        await held.at["agent-1"].wait()
        revise, decision = live._runs["agent-1"], live._decisions["agent-1"]  # pyright: ignore[reportPrivateUsage]
        revise.cancel()
        held.open("agent-1")
        await asyncio.wait({revise, decision})
        assert revise.cancelled() and decision.cancelled()
        assert resolutions(s) == [("change-1", "changes_requested")]
        assert state_of(s) == "unproposed"
        assert s.events()[-1].type == "change.resolved"  # no `revising`, nothing after the request
        assert live._runs == {} and live._decisions == {}  # pyright: ignore[reportPrivateUsage]


async def test_shutdown_before_the_request_is_taken_leaves_the_change_ready(
    tmp_path: Path,
) -> None:
    # The decision still waits for main's lock: nothing was sent, so the cancel puts the change
    # back, and it can be asked again.
    model = scripted_a()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        live = s.live
        await start(s)
        first = await ready(s)
        probe = ProbeLock()
        live.workspace._locks._main._lock = probe  # pyright: ignore[reportPrivateUsage]
        async with live.workspace.locks(main=True):
            live.request_changes("change-1", "Shorter, please.")
            await probe.contended.wait()
            revise = live._runs["agent-1"]  # pyright: ignore[reportPrivateUsage]
            revise.cancel()
            await asyncio.wait({revise})
        assert state_of(s) == "ready"
        assert resolutions(s) == [] and live._decisions == {}  # pyright: ignore[reportPrivateUsage]
        live.request_changes("change-1", "Shorter, please.")
        events = await ready(s, after=first[-1].seq)
        assert resolutions(s) == [("change-1", "changes_requested")] and events


async def test_shutdown_before_revise_starts_ends_the_change_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `_revise` is cancelled before its first step, so its body and handler never run: only
    # `_stop_runs` can settle the decision held in the publish, and end the change it left
    # `working`.
    revisions: list[object] = []
    real_revise = AgentRun.revise

    async def counting(self: AgentRun, comment: str, note: str | None) -> Any:
        revisions.append(comment)
        return await real_revise(self, comment, note)

    monkeypatch.setattr(AgentRun, "revise", counting)
    model = scripted_a()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
        HeldRequests(s.store, monkeypatch) as held,
    ):
        live = s.live
        manager = live._manager  # pyright: ignore[reportPrivateUsage]
        assert manager is not None
        await start(s)
        await ready(s)
        # What `request_changes` does, in two steps: the decision first, held in its publish.
        decision = manager.request_changes("change-1", "Shorter, please.")
        live._decisions["agent-1"] = decision  # pyright: ignore[reportPrivateUsage]
        await held.at["agent-1"].wait()
        agent, run = live._agents["agent-1"], live._kept["agent-1"]  # pyright: ignore[reportPrivateUsage]
        revise = asyncio.create_task(live._revise(manager, agent, run, decision))  # pyright: ignore[reportPrivateUsage]
        live._runs["agent-1"] = revise  # pyright: ignore[reportPrivateUsage]
        revise.cancel()  # no `await` since `create_task`
        stopping = asyncio.create_task(live._stop_runs())  # pyright: ignore[reportPrivateUsage]
        held.open("agent-1")
        await stopping
        assert revise.cancelled() and decision.cancelled() and revisions == []
        assert resolutions(s) == [("change-1", "changes_requested")]
        assert state_of(s) == "unproposed"
        assert s.events()[-1].type == "change.resolved"
        assert live._runs == {} and live._decisions == {}  # pyright: ignore[reportPrivateUsage]


async def test_shutdown_drains_decisions_that_pop_meanwhile(
    tmp_path: Path, slot_b_open: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two requests from `ready`. The first holds main's lock in its publish; the second waits for
    # that lock. The shutdown cancels both: the second ends and its `_revise` pops its entry while
    # the drain still waits for the first, which is held. The drain goes on from its snapshot.
    models = {
        "A": ScriptedModel(parse_script(revisable())),
        "B": ScriptedModel(parse_script(revisable_b())),
    }
    clients = BySlot(lambda _profile, slot: models[slot])
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path), clients) as s,
        HeldRequests(s.store, monkeypatch) as held,
    ):
        live = s.live
        await start(s, slot="A")
        await start(s, slot="B")
        for agent in ("agent-1", "agent-2"):
            await ready(s, agent)
        signals = PopSignals()
        live._decisions = signals  # pyright: ignore[reportPrivateUsage]
        live.request_changes("change-1", "Shorter, please.")
        await held.at["agent-1"].wait()
        live.request_changes("change-2", "Shorter, please.")
        stopping = asyncio.create_task(live._stop_runs())  # pyright: ignore[reportPrivateUsage]
        await signals.popped["agent-2"].wait()  # gone, while the drain waits for the first
        assert not stopping.done() and set(signals) == {"agent-1"}
        held.open("agent-1")
        await stopping
        assert resolutions(s) == [("change-1", "changes_requested")]
        assert [state_of(s, agent) for agent in ("agent-1", "agent-2")] == ["unproposed", "ready"]
        assert live._runs == {} and live._decisions == {}  # pyright: ignore[reportPrivateUsage]
