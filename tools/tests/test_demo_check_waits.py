"""Deadline precedence at blocking operations' return boundaries, without real-time races."""

import contextlib
import sys
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_check_io as io
import demo_check_phases as phases
import smoke
from oversight_contracts.commands import AgentCreated


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> io.OverallClock:
    now = [0.0]

    def advance(*args: Any) -> None:
        now[0] = 2.0

    timer = SimpleNamespace(monotonic=lambda: now[0], sleep=advance)
    monkeypatch.setattr(io, "time", timer)
    monkeypatch.setattr(phases, "time", timer)
    return io.OverallClock(started=0.0, deadline_s=1.0, phase="test wait")


def test_bounding_a_wait_uses_one_clock_reading(
    monkeypatch: pytest.MonkeyPatch, clock: io.OverallClock
) -> None:
    readings = iter([0.5, 2.0])
    monkeypatch.setattr(io.time, "monotonic", lambda: next(readings))
    assert clock.bounded(10) == 0.5  # Separate reads could give a negative socket timeout.
    with pytest.raises(smoke.SmokeError, match=r"overall deadline .* during test wait"):
        clock.bounded(10)


@pytest.mark.parametrize("method", ["read", "read1"])
@pytest.mark.parametrize("status", [200, 404])
def test_http_eof_after_the_deadline_names_the_overall_clock(
    monkeypatch: pytest.MonkeyPatch, clock: io.OverallClock, method: str, status: int
) -> None:
    def eof(*args: Any) -> bytes:
        io.time.sleep(2)
        return b""

    response: Any = SimpleNamespace(
        status=status, headers={}, closed=False, close=lambda: None, **{method: eof}
    )

    def urlopen(*args: Any, **kwargs: Any) -> Any:
        if status == 404:
            raise urllib.error.HTTPError("http://test", status, "missing", Message(), response)
        return contextlib.closing(response)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(smoke.SmokeError, match=r"overall deadline .* during test wait"):
        io.request("http://test", "/slow", clock=clock)


@pytest.mark.parametrize("status", [200, None])
def test_graph_fetch_checks_the_clock_before_interpreting_the_reply(
    monkeypatch: pytest.MonkeyPatch, clock: io.OverallClock, status: int | None
) -> None:
    def fetch(*args: Any) -> tuple[int | None, Any]:
        phases.time.sleep(2)
        return status, {}  # Even an invalid body cannot hide an expired deadline.

    monkeypatch.setattr(smoke, "fetch_json", fetch)
    with pytest.raises(smoke.SmokeError, match=r"overall deadline .* during test wait"):
        phases.graph_of("http://test", clock)


@pytest.mark.parametrize("failure", [TimeoutError("future timed out"), smoke.SmokeError("reader")])
@pytest.mark.parametrize("expired", [True, False])
def test_parallel_agent_wait_checks_the_clock_before_reporting_failure(
    monkeypatch: pytest.MonkeyPatch, clock: io.OverallClock, failure: Exception, expired: bool
) -> None:
    class Pool:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "Pool":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def submit(self, *args: Any) -> SimpleNamespace:
            return SimpleNamespace(result=self.result)

        def result(self, *, timeout: float) -> Any:
            assert timeout == 1.0
            if expired:
                phases.time.sleep(2)
            raise failure

    monkeypatch.setattr(phases, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(phases, "graph_of", lambda *args: SimpleNamespace(seq=0))
    expected = smoke.SmokeError if expired else type(failure)
    message = "overall deadline .* during A/B: parallel POST /agents" if expired else str(failure)
    with pytest.raises(expected, match=message):
        phases.check_level2("http://test", cast(Any, None), cast(Any, None), clock, Path("unused"))


@pytest.mark.parametrize("expired", [True, False])
def test_worktree_cleanup_checks_the_clock_after_the_last_poll(
    monkeypatch: pytest.MonkeyPatch, clock: io.OverallClock, tmp_path: Path, expired: bool
) -> None:
    who = AgentCreated(agent_id="agent-2", change_id="change-2", slot="B")
    (tmp_path / "worktrees" / who.agent_id).mkdir(parents=True)
    events = iter(
        [
            SimpleNamespace(seq=1),  # change.updated
            SimpleNamespace(seq=2, payload=SimpleNamespace(result="ok")),  # edit step
            SimpleNamespace(seq=3),  # stopped
            SimpleNamespace(payload=SimpleNamespace(decision="rejected", reason="stopped")),
        ]
    )
    stream = SimpleNamespace(wait=lambda *a, **kw: next(events), seen=[])
    monkeypatch.setattr(phases, "request_revision", lambda *a: SimpleNamespace(seq=0))
    monkeypatch.setattr(phases, "pending_nodes", lambda *a: {"pending"})
    monkeypatch.setattr(phases, "graph_of", lambda *a: SimpleNamespace(seq=0))
    monkeypatch.setattr(phases, "request", lambda *a, **kw: io.Reply(202, {}, b""))
    if not expired:
        monkeypatch.setattr(phases, "DECIDE_DEADLINE", 0.5)

        # The poll's local deadline elapses while the overall clock is still live.
        def local_timeout(_: float) -> None:
            monkeypatch.setattr(phases.time, "monotonic", lambda: 0.75)

        monkeypatch.setattr(phases.time, "sleep", local_timeout)
    message = (
        "overall deadline .* during B: stop during work and discard"
        if expired
        else "B worktree still exists after Stop"
    )
    with pytest.raises(smoke.SmokeError, match=message):
        phases.stop_working_b(
            "http://test",
            cast(Any, stream),
            clock,
            who,
            SimpleNamespace(payload=SimpleNamespace(revision=1)),
            tmp_path,
        )
