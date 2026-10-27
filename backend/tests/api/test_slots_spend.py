"""Two agents and one ledger (docs/plans/two-slots.md, section 4): only a metered agent's calls are
written to it, every tick carries the one shared total, and a lost write halts the agent whose call
it was, on that agent's own profile.

Slot B is closed by a constant until a merge on a moved main is tested; `slot_b_open` opens it."""

import asyncio
import os
import threading
from pathlib import Path
from typing import Literal

import pytest
from live_helpers import (
    DEADLINE_S,
    FAKE_SCRIPT,
    BySlot,
    GatedModel,
    Stepped,
    is_type,
    live_server,
    meta_settings,
    read_ledger,
    script_json,
    start,
    write_ledger,
)

from oversight.agents import MUSE_SPARK, LedgerState, ScriptedModel, parse_script
from oversight_contracts.events import ModelProfile

pytestmark = pytest.mark.anyio

USAGE = {"input_tokens": 1000, "output_tokens": 100, "cached_tokens": 0}
CALL_USD = (1000 * 0.10 + 100 * 0.20) / 1_000_000
"""One scripted call at `meta_settings`' prices."""
LOOK_THEN_FINISH = script_json(
    "List the files, then finish.",
    [
        {"tool_calls": [{"name": "list_files", "arguments": {}}], "usage": USAGE},
        {"tool_calls": [{"name": "finish", "arguments": {"summary": "Done."}}], "usage": USAGE},
    ],
)


def model_for(profile: ModelProfile, script: str) -> ScriptedModel:
    return ScriptedModel(parse_script(script), profile)


@pytest.mark.usefixtures("baseten_verified")
@pytest.mark.parametrize("unmetered", ["fake", "muse-spark-open"])
async def test_only_metered_ticks_reach_the_ledger(
    tmp_path: Path, slot_b_open: None, unmetered: str
) -> None:
    # A on a profile that spends no Meta credits (the fake model, or Muse Spark Open on Baseten),
    # B on Muse Spark, both scripted with usage. A's ticks go out with no cost and no ledger
    # write; the file gains B's calls alone and records no unknown call.
    settings = meta_settings(
        tmp_path,
        agent_model=unmetered,
        agent_model_b="muse-spark",
        fake_script=FAKE_SCRIPT,
        baseten_api_key="test-key",
    )
    write_ledger(settings, {"spent_usd": 1.0, "unknown": []})
    clients = BySlot(lambda profile, _slot: model_for(profile, LOOK_THEN_FINISH))
    async with asyncio.timeout(DEADLINE_S), live_server(settings, clients) as s:
        assert (await start(s, slot="A")).status_code == 201
        assert (await start(s, slot="B")).status_code == 201
        for agent in ("agent-1", "agent-2"):
            await s.until(is_type("agent.status", agent_id=agent, state="ready_for_review"))
        ticks = [e.payload for e in s.events() if e.type == "usage.tick"]
    assert sorted((t.agent_id, t.cost_usd is None) for t in ticks) == [
        ("agent-1", True),
        ("agent-1", True),
        ("agent-2", False),
        ("agent-2", False),
    ]
    assert read_ledger(settings) == {
        "spent_usd": pytest.approx(1.0 + 2 * CALL_USD, rel=0, abs=1e-12),
        "unknown": [],
    }


async def test_two_agents_share_one_growing_total(tmp_path: Path, slot_b_open: None) -> None:
    # Both on Muse Spark, their calls interleaved A B A B. Each tick carries the one total, which
    # never goes down along `seq`, and the last one is the start plus every call of both.
    settings = meta_settings(tmp_path)
    write_ledger(settings, {"spent_usd": 10.0, "unknown": []})
    models = {slot: Stepped(parse_script(LOOK_THEN_FINISH), MUSE_SPARK) for slot in ("A", "B")}
    clients = BySlot(lambda _profile, slot: models[slot])
    async with asyncio.timeout(DEADLINE_S), live_server(settings, clients) as s:
        assert (await start(s)).status_code == 201
        assert (await start(s)).status_code == 201
        # A tick goes out before the step of its call, so the step says the tick is in the log.
        for step in (1, 2):
            for slot, agent in (("A", "agent-1"), ("B", "agent-2")):
                models[slot].permits.release()
                await s.until(is_type("agent.step", agent_id=agent, step=step))
        ticks = [e.payload for e in s.events() if e.type == "usage.tick"]
    assert [t.agent_id for t in ticks] == ["agent-1", "agent-2", "agent-1", "agent-2"]
    assert [t.spent_usd for t in ticks] == pytest.approx(
        [10.0 + n * CALL_USD for n in (1, 2, 3, 4)], rel=0, abs=1e-9
    )
    assert read_ledger(settings)["spent_usd"] == pytest.approx(10.0 + 4 * CALL_USD, rel=0, abs=1e-9)


async def test_a_lost_ledger_halts_b_with_its_own_profile(
    tmp_path: Path, slot_b_open: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A runs the fake model and stays working. B, on Muse Spark, makes a call the ledger cannot
    # record: B halts, and its card names Muse Spark and B's change, not A's model or change.
    settings = meta_settings(
        tmp_path, agent_model="fake", agent_model_b="muse-spark", fake_script=FAKE_SCRIPT
    )
    write_ledger(settings, {"spent_usd": 1.0, "unknown": []})
    gated = GatedModel()
    clients = BySlot(
        lambda profile, slot: gated if slot == "A" else model_for(profile, LOOK_THEN_FINISH)
    )
    async with asyncio.timeout(DEADLINE_S), live_server(settings, clients) as s:
        assert (await start(s, slot="A")).status_code == 201
        await gated.entered.wait()

        def broken_fsync(fd: int) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(os, "fsync", broken_fsync)
        created = (await start(s, slot="B")).json()
        events = await s.until(is_type("agent.status", state="halted"))
        monkeypatch.undo()
        [fatal] = [e.payload for e in events if e.type == "error" and e.payload.fatal]
        assert (fatal.agent_id, fatal.change_id) == ("agent-2", created["change_id"])
        halted = events[-1].payload
        assert (halted.agent_id, halted.slot, halted.model) == ("agent-2", "B", "muse-spark")
        assert halted.change_id == created["change_id"]
        # The unmetered agent runs on, and the metered start stays refused.
        assert not gated.cancelled.is_set()
        assert s.live.brake_reason() is not None


class ProbeLock(asyncio.Lock):
    """The ledger's lock, with an event that is set when a call finds it held."""

    def __init__(self) -> None:
        super().__init__()
        self.contended = asyncio.Event()

    async def acquire(self) -> Literal[True]:
        if self.locked():
            self.contended.set()
        return await super().acquire()


@pytest.mark.parametrize("b_write_fails", [False, True], ids=["b-write-ok", "b-write-fails"])
async def test_a_cancel_waiting_for_the_ledger_lock_keeps_the_spend(
    tmp_path: Path, slot_b_open: None, monkeypatch: pytest.MonkeyPatch, b_write_fails: bool
) -> None:
    # Two metered agents. A's call holds the ledger's lock in its write; B's call waits for the
    # lock, and B's run is cancelled meanwhile. B's call was paid for: it is written all the same,
    # and when that write fails the brake closes and B halts, as for any lost write.
    settings = meta_settings(tmp_path)
    write_ledger(settings, {"spent_usd": 1.0, "unknown": []})
    one_call = script_json(
        "Finish at once.",
        [{"tool_calls": [{"name": "finish", "arguments": {"summary": "Done."}}], "usage": USAGE}],
    )
    models = {slot: Stepped(parse_script(one_call), MUSE_SPARK) for slot in ("A", "B")}
    clients = BySlot(lambda _profile, slot: models[slot])
    async with asyncio.timeout(DEADLINE_S), live_server(settings, clients) as s:
        ledger = s.live.ledger
        lock = ProbeLock()
        ledger._lock = lock  # pyright: ignore[reportPrivateUsage]
        entered, release, writes = threading.Event(), threading.Event(), []
        real_write = ledger.write

        def write(state: LedgerState) -> None:
            writes.append(state.spent_usd)
            if len(writes) == 1:  # A's: held until the test lets it go
                entered.set()
                assert release.wait(DEADLINE_S)
            elif b_write_fails:
                raise OSError("disk full")
            real_write(state)

        monkeypatch.setattr(ledger, "write", write)
        # While a write is in flight the brake reads closed, so both runs start first.
        assert (await start(s, slot="A")).status_code == 201
        created = (await start(s, slot="B")).json()
        models["A"].permits.release()
        assert await asyncio.to_thread(entered.wait, DEADLINE_S)  # A's call is in the file's write
        models["B"].permits.release()
        await lock.contended.wait()  # B's call is waiting for the lock A's write holds
        run_a, run_b = s.live._runs["agent-1"], s.live._runs["agent-2"]  # pyright: ignore[reportPrivateUsage]
        run_b.cancel()
        release.set()
        await asyncio.wait({run_a, run_b})
        assert run_b.cancelled()
        ticks = [e.payload for e in s.events() if e.type == "usage.tick"]
        assert [t.agent_id for t in ticks] == ["agent-1"]  # B's tick never went out as recorded
        if not b_write_fails:
            assert read_ledger(settings)["spent_usd"] == pytest.approx(
                1.0 + 2 * CALL_USD, rel=0, abs=1e-9
            )
            assert s.live.brake_reason() is None
            return
        assert read_ledger(settings)["spent_usd"] == pytest.approx(1.0 + CALL_USD, rel=0, abs=1e-9)
        assert s.live.brake_reason() is not None
        events = await s.until(is_type("agent.status", agent_id="agent-2", state="halted"))
        [fatal] = [e.payload for e in events if e.type == "error" and e.payload.fatal]
        assert (fatal.agent_id, fatal.change_id) == ("agent-2", created["change_id"])
        halted = events[-1].payload
        assert (halted.slot, halted.model) == ("B", "muse-spark")
