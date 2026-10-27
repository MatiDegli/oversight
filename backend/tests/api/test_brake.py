"""The spending brake: configured or closed, the ledger across restarts, warnings, settling."""

import asyncio
import json
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from live_helpers import (
    DEADLINE_S,
    Scripts,
    is_type,
    live_server,
    live_settings,
    meta_settings,
    script_json,
)

from oversight.agents import (
    MUSE_SPARK,
    LedgerFile,
    LedgerState,
    Message,
    ModelClient,
    ModelReply,
    ScriptedModel,
    ToolSchema,
    UnknownUsage,
    parse_script,
)
from oversight.app import ledger_command
from oversight.settings import Settings
from oversight.workspace import WorkspaceBusy
from oversight_contracts.commands import ErrorResponse
from oversight_contracts.events import ModelProfile

pytestmark = pytest.mark.anyio

USAGE = {"input_tokens": 1000, "output_tokens": 100, "cached_tokens": 0}
METERED = script_json(
    "Finish at once.",
    [{"tool_calls": [{"name": "finish", "arguments": {"summary": "Done."}}], "usage": USAGE}],
)
TWO_CALLS = script_json(
    "List the files, then finish.",
    [
        {"tool_calls": [{"name": "list_files", "arguments": {}}], "usage": USAGE},
        {"tool_calls": [{"name": "finish", "arguments": {"summary": "Done."}}], "usage": USAGE},
    ],
)
NO_USAGE = script_json(
    "Finish at once.", [{"tool_calls": [{"name": "finish", "arguments": {"summary": "Done."}}]}]
)


def write_ledger(settings: Settings, body: object) -> None:
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    text = body if isinstance(body, str) else json.dumps(body)
    (settings.runtime_dir / "ledger.json").write_text(text)


def read_ledger(settings: Settings) -> dict[str, Any]:
    return json.loads((settings.runtime_dir / "ledger.json").read_text())


async def start_code(settings: Settings, scripts: Scripts) -> tuple[int, str | None]:
    async with live_server(settings, scripts) as s:
        response = await s.client.post("/agents", json={"task": "t"})
        if response.status_code == 201:
            await s.until(is_type("agent.status", state="ready_for_review"))
            return 201, None
        return response.status_code, ErrorResponse.model_validate(response.json()).error.code


@pytest.mark.parametrize(
    ("overrides", "ledger"),
    [
        ({"budget_usd": None}, None),
        ({"output_usd_per_m": None}, None),
        ({}, "not json"),
        ({}, {"spent_usd": "12", "unknown": []}),
        ({}, {"spent_usd": 1.0, "unknown": [{"run_id": "r", "agent_id": "agent-1", "ts": "t"}]}),
        ({}, {"spent_usd": 135.01, "unknown": []}),
    ],
    ids=["no-budget", "no-price", "not-json", "malformed", "unknown-entry", "above-90"],
)
async def test_a_closed_brake_refuses_before_any_client(
    tmp_path: Path, overrides: dict[str, Any], ledger: object
) -> None:
    settings = meta_settings(tmp_path, **overrides)
    if ledger is not None:
        write_ledger(settings, ledger)
    scripts = Scripts([METERED], MUSE_SPARK)
    async with asyncio.timeout(DEADLINE_S):
        assert await start_code(settings, scripts) == (403, "spend_brake")
    assert scripts.calls == 0


async def test_an_unreadable_ledger_is_reported_at_start(tmp_path: Path) -> None:
    settings = meta_settings(tmp_path)
    write_ledger(settings, "not json")
    async with asyncio.timeout(DEADLINE_S), live_server(settings, Scripts([], MUSE_SPARK)) as s:
        errors = [e for e in s.events() if e.type == "error"]
        assert [e.payload.source for e in errors] == ["server"]
        assert "ledger" in errors[0].payload.message
    assert (settings.runtime_dir / "ledger.json").read_text() == "not json"


@pytest.mark.parametrize(
    ("spent", "brake"), [(75.0, "warn_50"), (120.0, "warn_80"), (135.0, "blocked")]
)
async def test_from_half_the_budget_runs_start_and_ticks_warn(
    tmp_path: Path, spent: float, brake: str
) -> None:
    # 135 of 150 is exactly 90%: it starts, and its first call takes it past, so the tick says
    # blocked while the run goes on.
    settings = meta_settings(tmp_path)
    write_ledger(settings, {"spent_usd": spent, "unknown": []})
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(settings, Scripts([METERED], MUSE_SPARK)) as s,
    ):
        assert (await s.client.post("/agents", json={"task": "t"})).status_code == 201
        [tick] = [e for e in await s.until(is_type("usage.tick")) if e.type == "usage.tick"]
    # 1000 input and 100 output tokens at 0.10 and 0.20 per million.
    cost = (1000 * 0.10 + 100 * 0.20) / 1_000_000
    assert tick.payload.brake == brake
    assert tick.payload.cost_usd == pytest.approx(cost, rel=0, abs=1e-12)
    assert tick.payload.spent_usd == pytest.approx(spent + cost, rel=0, abs=1e-9)
    assert read_ledger(settings) == {
        "spent_usd": pytest.approx(spent + cost, rel=0, abs=1e-9),
        "unknown": [],
    }


async def test_replies_without_usage_refuse_the_next_start_until_settled(tmp_path: Path) -> None:
    settings = meta_settings(tmp_path)
    async with asyncio.timeout(DEADLINE_S):
        async with live_server(settings, Scripts([NO_USAGE], MUSE_SPARK)) as s:
            created = (await s.client.post("/agents", json={"task": "t"})).json()
            await s.until(is_type("agent.status", state="ready_for_review"))
            [entry] = read_ledger(settings)["unknown"]
            assert entry["run_id"] == s.live.run_id and entry["agent_id"] == "agent-1"
            assert (
                await s.client.post(f"/changes/{created['change_id']}/reject")
            ).status_code == 202
            await s.until(is_type("agent.status", state="done"))
            refused = await s.client.post("/agents", json={"task": "t"})
            assert ErrorResponse.model_validate(refused.json()).error.code == "spend_brake"
            with pytest.raises(WorkspaceBusy):
                await asyncio.to_thread(ledger_command, settings, 0.5)
        # A restart, and its reset, keep the ledger.
        assert await start_code(settings, Scripts([NO_USAGE], MUSE_SPARK)) == (403, "spend_brake")
        printed = await asyncio.to_thread(ledger_command, settings, 0.5)
        assert json.loads(printed) == {"spent_usd": 0.5, "unknown": []}
        assert await start_code(settings, Scripts([METERED], MUSE_SPARK)) == (201, None)


async def test_the_fake_model_is_never_braked(tmp_path: Path) -> None:
    settings = live_settings(tmp_path)
    write_ledger(settings, "not json")
    async with asyncio.timeout(DEADLINE_S):
        assert await start_code(settings, Scripts([NO_USAGE])) == (201, None)
    assert (settings.runtime_dir / "ledger.json").read_text() == "not json"


def test_a_ledger_write_failing_before_the_replace_keeps_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = LedgerFile(tmp_path / "ledger.json")
    ledger.write(LedgerState(2.0, ()))
    before = ledger.path.read_text()

    def broken_fsync(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", broken_fsync)
    entry = UnknownUsage("run-20261026-000000", "agent-1", None, "2026-10-26T00:00:00.000Z")
    with pytest.raises(OSError, match="disk full"):
        ledger.write(LedgerState(3.0, (entry,)))
    assert ledger.path.read_text() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ledger.json"]
    assert ledger.read() == LedgerState(2.0, ())


def test_settling_sets_the_spend_and_clears_unknown_calls(tmp_path: Path) -> None:
    settings = live_settings(tmp_path)
    write_ledger(
        settings,
        {
            "spent_usd": 9.0,
            "unknown": [{"run_id": "r", "agent_id": "agent-1", "model": None, "ts": "t"}],
        },
    )
    assert json.loads(ledger_command(settings, None))["spent_usd"] == 9.0
    assert json.loads(ledger_command(settings, 40.0)) == {"spent_usd": 40.0, "unknown": []}


async def test_the_fake_model_ticks_say_ok_without_a_budget(tmp_path: Path) -> None:
    # The server refuses nothing on the fake profile, so a tick must not report a block.
    settings = live_settings(tmp_path)
    assert settings.budget_usd is None
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(settings, Scripts([NO_USAGE])) as s,
    ):
        assert (await s.client.post("/agents", json={"task": "t"})).status_code == 201
        [tick] = [e for e in await s.until(is_type("usage.tick")) if e.type == "usage.tick"]
    assert (tick.payload.brake, tick.payload.budget_usd) == ("ok", 0.0)


@dataclass
class CountingModel:
    """A scripted model that counts its calls and remembers the task that made the first."""

    inner: ScriptedModel
    calls: int = 0
    task: "asyncio.Task[Any] | None" = None

    @property
    def profile(self) -> ModelProfile:
        return self.inner.profile

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> ModelReply:
        self.calls += 1
        self.task = self.task or asyncio.current_task()
        return await self.inner.complete(messages, tools)


def fail_ledger_writes(monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    """`fsync` or `os.replace` of the ledger raises, as a full disk or a read-only mount does."""
    real_replace = os.replace

    def broken_fsync(fd: int) -> None:
        raise OSError("disk full")

    def broken_replace(src: Any, dst: Any, **kwargs: Any) -> None:
        if str(dst).endswith("ledger.json"):
            raise OSError("read-only file system")
        real_replace(src, dst, **kwargs)

    if step == "fsync":
        monkeypatch.setattr(os, "fsync", broken_fsync)
    else:
        monkeypatch.setattr(os, "replace", broken_replace)


@pytest.mark.parametrize("step", ["fsync", "replace"])
async def test_a_tick_the_ledger_cannot_record_stops_the_run_and_closes_the_brake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    settings = meta_settings(tmp_path)
    write_ledger(settings, {"spent_usd": 1.0, "unknown": []})
    model = CountingModel(ScriptedModel(parse_script(TWO_CALLS), MUSE_SPARK))
    client: ModelClient = model
    async with asyncio.timeout(DEADLINE_S), live_server(settings, lambda _: client) as s:
        with monkeypatch.context() as patch:
            fail_ledger_writes(patch, step)
            created = (await s.client.post("/agents", json={"task": "t"})).json()
            events = await s.until(is_type("error"))
            assert model.task is not None
            await asyncio.wait({model.task})
        [error] = [e for e in events if e.type == "error"]
        assert (error.payload.source, error.payload.fatal) == ("server", True)
        ids = (error.payload.agent_id, error.payload.change_id)
        assert ids == ("agent-1", created["change_id"])
        assert "ledger" in error.payload.message
        assert "settle with Meta's console total before the next run" in error.payload.message
        # The run stopped as a shutdown stops it, at the call that could not be recorded: no
        # tick went out as recorded, and the model was not called again.
        assert model.task.cancelled() and model.calls == 1
        assert [e.type for e in s.events() if e.type == "usage.tick"] == []
        assert read_ledger(settings) == {"spent_usd": 1.0, "unknown": []}
        # Writes work again, and the brake stays closed for this process.
        refused = await s.client.post("/agents", json={"task": "t"})
        body = ErrorResponse.model_validate(refused.json()).error
        assert (refused.status_code, body.code) == (403, "spend_brake")
        assert "settle with Meta's console total before the next run" in body.message
        # The slot is released the usual way, by rejecting the change.
        rejected = await s.client.post(f"/changes/{created['change_id']}/reject")
        assert rejected.status_code == 202
        await s.until(is_type("agent.status", state="done"))
        assert s.live._slot is None  # pyright: ignore[reportPrivateUsage]
    assert model.calls == 1


class GatedWrites:
    """A ledger whose write of one chosen total waits on `release`, in its thread, and then
    succeeds or fails. `entered` is set once that write is waiting; `trace` lists the writes."""

    def __init__(
        self, ledger: LedgerFile, monkeypatch: pytest.MonkeyPatch, gated: float, fail: bool = False
    ) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.trace: list[str] = []
        self.draining = asyncio.Event()
        """Set when a cancelled `record` starts waiting for its write (`watch_drain`)."""
        real_write = ledger.write

        def write(state: LedgerState) -> None:
            self.trace.append(f"enter {state.spent_usd}")
            if state.spent_usd == gated:
                self.entered.set()
                assert self.release.wait(DEADLINE_S)
                if fail:
                    raise OSError("disk full")
            real_write(state)
            self.trace.append(f"exit {state.spent_usd}")

        monkeypatch.setattr(ledger, "write", write)

    def watch_drain(self, ledger: LedgerFile, monkeypatch: pytest.MonkeyPatch) -> None:
        real_drain = ledger._drain  # pyright: ignore[reportPrivateUsage]

        async def drain(write: Any, new: LedgerState) -> None:
            self.draining.set()
            await real_drain(write, new)

        monkeypatch.setattr(ledger, "_drain", drain)

    async def wait_entered(self) -> None:
        assert await asyncio.to_thread(self.entered.wait, DEADLINE_S)


ENTRY = UnknownUsage("run-20261026-000000", "agent-1", None, "2026-10-26T00:00:00.000Z")


async def test_a_cancelled_ledger_write_ends_before_the_lock_is_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = LedgerFile(tmp_path / "ledger.json")
    ledger.write(LedgerState(1.0, ()))
    await ledger.load()
    gate = GatedWrites(ledger, monkeypatch, gated=2.0)
    async with asyncio.timeout(DEADLINE_S):
        first = asyncio.create_task(ledger.record(1.0, ENTRY))
        await gate.wait_entered()
        first.cancel()
        second = asyncio.create_task(ledger.record(1.0, ENTRY))
        gate.release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        # The cancelled write landed, and the next one started only after it ended.
        assert await second == LedgerState(3.0, ())
    assert gate.trace == ["enter 2.0", "exit 2.0", "enter 3.0", "exit 3.0"]
    assert ledger.read() == LedgerState(3.0, ())


async def test_a_ledger_write_cancelled_twice_is_still_waited_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = LedgerFile(tmp_path / "ledger.json")
    ledger.write(LedgerState(1.0, ()))
    await ledger.load()
    gate = GatedWrites(ledger, monkeypatch, gated=2.0)
    gate.watch_drain(ledger, monkeypatch)
    async with asyncio.timeout(DEADLINE_S):
        first = asyncio.create_task(ledger.record(1.0, ENTRY))
        await gate.wait_entered()
        first.cancel()
        await gate.draining.wait()
        first.cancel()  # cancelled again while it waits: still no early end
        gate.release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert gate.trace == ["enter 2.0", "exit 2.0"]  # `first` ended after the write did
    assert ledger.state == LedgerState(2.0, ())
    assert ledger.read() == LedgerState(2.0, ())


async def test_a_cancelled_ledger_write_that_fails_leaves_the_state_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = LedgerFile(tmp_path / "ledger.json")
    ledger.write(LedgerState(1.0, ()))
    await ledger.load()
    gate = GatedWrites(ledger, monkeypatch, gated=2.0, fail=True)
    async with asyncio.timeout(DEADLINE_S):
        first = asyncio.create_task(ledger.record(1.0, ENTRY))
        await gate.wait_entered()
        first.cancel()
        gate.release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
    assert ledger.state is None
    assert ledger.read() == LedgerState(1.0, ())
