"""Setup and grading cleanup must finish even when peers stop making progress."""

import asyncio
import json
import signal
import time

import pytest
from live_helpers import Scripts, live_server, live_settings  # pyright: ignore[reportMissingImports]
from tools.eval import grade, session
from tools.eval.grade import Tariff
from tools.eval.run import execute
from tools.eval.tasks import BenchError, load_tasks


@pytest.mark.anyio
@pytest.mark.parametrize("pings", [False, True], ids=["silent", "ping-only"])
async def test_setup_times_out_before_start_when_replay_stalls(pings, tmp_path, monkeypatch):
    monkeypatch.setattr(session, "SETUP_TIMEOUT_S", 3, raising=False)
    factory = Scripts()
    out = tmp_path / "results" / "r.jsonl"
    async with live_server(live_settings(tmp_path), factory) as server:
        original = server.app.middleware_stack
        ping_count = 0

        async def stalled_replay(scope, receive, send):
            nonlocal ping_count
            if scope["type"] != "http" or scope["path"] != "/events":
                await original(scope, receive, send)
                return
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")],
                }
            )
            # Preflight receives its initial snapshot; setup never gets its deltas.
            for stored in server.store._events[:2]:
                body = f"id: {stored.seq}\ndata: {stored.envelope}\n\n".encode()
                await send({"type": "http.response.body", "body": body, "more_body": True})
            while True:
                try:
                    message = await asyncio.wait_for(receive(), 0.01)
                    if message["type"] == "http.disconnect":
                        return
                except TimeoutError:
                    if pings:
                        ping_count += 1
                        await send(
                            {"type": "http.response.body", "body": b": ping\n\n", "more_body": True}
                        )

        server.app.middleware_stack = stalled_replay
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                execute(
                    str(server.client.base_url),
                    "fake",
                    1,
                    out,
                    [load_tasks()[0]],
                    Tariff(input=0, output=0),
                ),
                10,
            )
        assert time.monotonic() - started < 8, "setup depended on the test's outer backstop"
        assert factory.calls == 0
        assert not out.read_text()
        assert json.loads((out.parent / "ledger.json").read_text())["records"] == []
        assert len(server.events()) > 2
        raw_events = out.parent / f"events-{server.store.run_id}.jsonl"
        assert len(raw_events.read_text().splitlines()) == 2
        assert bool(ping_count) == pings


@pytest.mark.anyio
@pytest.mark.parametrize("reapable", [True, False], ids=["reaped", "unreapable"])
async def test_grading_bounds_reap_after_kill(reapable, tmp_path, monkeypatch):
    monkeypatch.setattr(grade, "REAP_TIMEOUT_S", 0.01, raising=False)
    kills = []

    class Child:
        pid = 12345
        waits = 0

        async def wait(self):
            self.waits += 1
            if self.waits > 1 and reapable:
                return -signal.SIGKILL
            await asyncio.Event().wait()

    child = Child()

    async def spawn(*args, **kwargs):
        return child

    monkeypatch.setattr(grade.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(grade.os, "killpg", lambda pid, sig: kills.append((pid, sig)))
    evidence = tmp_path / "suite.txt"
    pending = grade.suite(tmp_path, ["tests"], evidence, deadline_s=0.01)
    if reapable:
        assert await asyncio.wait_for(pending, 1) is False
    else:
        with pytest.raises(BenchError, match=r"12345.*SIGKILL"):
            await asyncio.wait_for(pending, 1)
    assert child.waits == 2
    assert kills == [(child.pid, signal.SIGKILL)]
