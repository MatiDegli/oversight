"""Shared native spend history and exclusive writers, without model traffic."""

import asyncio
import json
import threading

import pytest
from live_helpers import live_settings

from oversight.agents import UnknownUsage
from oversight.app import LiveRun, create_app, ledger_command
from oversight.settings import Settings
from oversight.workspace import WorkspaceBusy, acquire_lock, release_lock


def test_ledger_directory_defaults_and_explicit_environment(tmp_path, monkeypatch):
    from dataclasses import replace

    runtime, shared = tmp_path / "runtime", tmp_path / "shared"
    monkeypatch.setenv("OVERSIGHT_RUNTIME_DIR", str(runtime))
    monkeypatch.delenv("OVERSIGHT_LEDGER_DIR", raising=False)
    assert Settings(runtime_dir=runtime).ledger_root == runtime
    assert Settings.load(tmp_path / "absent-config").ledger_root == runtime
    assert replace(Settings(runtime_dir=runtime), runtime_dir=shared).ledger_root == shared
    monkeypatch.setenv("OVERSIGHT_LEDGER_DIR", str(shared))
    assert Settings.load(tmp_path / "absent-config").ledger_root == shared


@pytest.mark.anyio
async def test_two_runtimes_keep_spend_and_unknowns_in_both_shared_ledgers(tmp_path):
    shared = tmp_path / "shared"
    entry = UnknownUsage("run", "agent-1", "fake", "2026-11-04T12:00:00Z")
    for name in ("first", "second"):
        live = LiveRun(live_settings(tmp_path / name, ledger_dir=shared))
        try:
            await live.startup()
            accounting = live.difference_decisions.accounting
            await accounting.load()
            assert live.ledger.path == shared / "ledger.json"
            assert accounting.ledger.path == shared / "baseten-decisions-ledger.json"
            assert accounting._recovery == shared / "baseten-decisions-ledger.unreliable"
            for ledger in (live.ledger, accounting.ledger):
                if name == "first":
                    await ledger.record(1.5, entry)
                    await ledger.record(None, entry)
                else:
                    assert ledger.state is not None
                    assert ledger.state.spent_usd == 1.5
                    assert ledger.state.unknown == (entry,)
            assert not (live._settings.runtime_dir / "ledger.json").exists()
        finally:
            await live.shutdown()
    # The marker is shared too, so another source cannot restore decision admission.
    (shared / "baseten-decisions-ledger.unreliable").write_text("preserve receipt")
    another = LiveRun(live_settings(tmp_path / "third", ledger_dir=shared))
    try:
        await another.startup()
        await another.difference_decisions.accounting.load()
        assert another.difference_decisions.accounting._lost
    finally:
        await another.shutdown()


@pytest.mark.parametrize("same", [True, False])
@pytest.mark.anyio
async def test_held_ledger_lock_refuses_startup_before_reset(tmp_path, same, capsys):
    runtime = tmp_path / ".runtime"
    shared = runtime if same else tmp_path / "shared"
    shared.mkdir(parents=True)
    fd = acquire_lock(shared / "ledger.lock")
    app = create_app(live_settings(tmp_path, ledger_dir=shared))
    try:
        with pytest.raises(WorkspaceBusy):
            async with app.router.lifespan_context(app):
                pytest.fail("started with a held ledger lock")
        assert capsys.readouterr().err.splitlines() == ["OVERSIGHT_REFUSAL=other"]
        assert not (runtime / "workspace").exists()
    finally:
        release_lock(fd)


@pytest.mark.parametrize("same", [True, False])
def test_settle_holds_both_locks_and_writes_shared_file(tmp_path, same):
    runtime = tmp_path / ".runtime"
    shared = runtime if same else tmp_path / "shared"
    shared.mkdir(parents=True)
    settings = Settings(runtime_dir=runtime, ledger_dir=shared)
    fd = acquire_lock(shared / "ledger.lock")
    try:
        with pytest.raises(WorkspaceBusy):
            ledger_command(settings, 2.0)
        assert not (shared / "ledger.json").exists()
    finally:
        release_lock(fd)
    assert json.loads(ledger_command(settings, 2.0)) == {"spent_usd": 2.0, "unknown": []}
    assert json.loads(ledger_command(settings, None))["spent_usd"] == 2.0
    server = acquire_lock(runtime / "server.lock")
    try:
        with pytest.raises(WorkspaceBusy):
            ledger_command(settings, 3.0)
    finally:
        release_lock(server)
    assert json.loads((shared / "ledger.json").read_text())["spent_usd"] == 2.0


@pytest.mark.anyio
async def test_server_keeps_shared_lock_until_pending_write_drains(tmp_path, monkeypatch):
    live = LiveRun(live_settings(tmp_path))
    await live.startup()
    entered, finish = threading.Event(), threading.Event()
    original = live.ledger.write

    def blocked(state):
        entered.set()
        assert finish.wait(10)
        original(state)

    monkeypatch.setattr(live.ledger, "write", blocked)
    entry = UnknownUsage("run", "agent-1", "fake", "2026-11-04T12:00:00Z")
    writing = asyncio.create_task(live.ledger.record(1.0, entry))
    shutdown = None
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        shutdown = asyncio.create_task(live.shutdown())
        # Wait until shutdown reaches its ledger drain, without releasing the disk writer.
        await asyncio.sleep(0.1)
        assert not shutdown.done()
        with pytest.raises(WorkspaceBusy):
            acquire_lock(live._settings.ledger_root / "ledger.lock")
    finally:
        finish.set()
        await writing
        if shutdown:
            await shutdown
        else:
            await live.shutdown()
    fd = acquire_lock(live._settings.ledger_root / "ledger.lock")
    release_lock(fd)
