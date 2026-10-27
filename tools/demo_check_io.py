"""Bounded HTTP and schema-validated SSE for the demo checker."""

import http.client
import json
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import TypeAdapter, ValidationError

import smoke
from oversight_contracts.events import ServerEvent

RUNS = 3
REQUEST_TIMEOUT = 10.0  # seconds one HTTP request may take
START_DEADLINE = 30.0  # seconds /health and /graph have to answer within
# POST /agents answers only once the demo venv is built, which the first run does: up to the
# server's own bounds for it (a 60 s probe, 120 s to create the venv, 300 s to install pytest).
START_AGENT_DEADLINE = 480.0
PROPOSE_DEADLINE = 240.0  # the agent's run: from the answer to POST /agents to change.proposed
DECIDE_DEADLINE = 60.0  # approve to change.resolved and graph.delta
STOP_DEADLINE = 20.0  # seconds the server gets after SIGTERM before SIGKILL
# UI assets, command boundaries (GET /graph), decisions, files and cleanup probes.
REQUESTS_PER_RUN = 60
OVERALL_MARGIN = 60.0  # slack over the worst-case sum, for process start and cleanup
# Worst case: every phase in every run takes its full deadline. START_AGENT_DEADLINE (the cold
# 480 s venv build, once in practice but allowed per run here) and PROPOSE_DEADLINE drive it.
PER_RUN_DEADLINE = (
    2 * START_DEADLINE
    + 2 * START_AGENT_DEADLINE
    + 3 * PROPOSE_DEADLINE  # initial A/B proposals and A's revision
    + 40 * DECIDE_DEADLINE  # correlated steps, usage, decisions, deltas and discard
    + 2 * STOP_DEADLINE  # SIGTERM wait, then the SIGKILL wait in smoke.stop_child
    + REQUESTS_PER_RUN * REQUEST_TIMEOUT
)
OVERALL_DEADLINE = RUNS * PER_RUN_DEADLINE + OVERALL_MARGIN
STREAM_IDLE = 120.0  # seconds a read on /events may wait: the server pings every 15
ASSET = re.compile(r'(?:src|href)="(/assets/[^"]+)"')
EVENTS: TypeAdapter[Any] = TypeAdapter(ServerEvent)


@dataclass
class OverallClock:
    """The check's own overall deadline, from OVERALL_DEADLINE. `phase` names the step in
    progress, so in a normal failure passing the deadline fails naming that step. Best effort
    only: the hard bound is the Makefile's outer timeout (see the module docstring)."""

    started: float = field(default_factory=time.monotonic)
    deadline_s: float = OVERALL_DEADLINE
    phase: str = "startup"

    def check(self, now: float | None = None) -> None:
        elapsed = (time.monotonic() if now is None else now) - self.started
        if elapsed > self.deadline_s:
            raise smoke.SmokeError(
                f"overall deadline {self.deadline_s:g} s exceeded during {self.phase} "
                f"after {elapsed:.0f} s"
            )

    def remaining(self, now: float | None = None) -> float:
        return self.deadline_s - ((time.monotonic() if now is None else now) - self.started)

    def bounded(self, timeout_s: float) -> float:
        """A wait inside this phase may take the smaller of its own timeout and what the
        overall deadline has left; an expired overall deadline fails here, naming the phase."""
        self.check()
        return min(timeout_s, self.remaining())

    def set_phase(self, phase: str) -> None:
        self.check()  # before renaming: an expiry belongs to the phase that ran out
        self.phase = phase


@dataclass(frozen=True)
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def request(
    base: str,
    path: str,
    *,
    post: object | None = None,
    put: object | None = None,
    timeout: float = REQUEST_TIMEOUT,
    clock: OverallClock | None = None,
) -> Reply:
    """GET, POST or PUT with a JSON body. An error status is a reply, not an
    exception; no answer at all within `timeout` seconds is SmokeError. The timeout bounds the
    reply in total, not just idleness between bytes, and never outlives `clock`."""
    if post is not None and put is not None:
        raise ValueError("choose either post or put")
    method = "PUT" if put is not None else "GET" if post is None else "POST"
    body = put if put is not None else post
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {} if data is None else {"Content-Type": "application/json"}
    call = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    timeout = timeout if clock is None else clock.bounded(timeout)
    deadline = time.monotonic() + timeout
    try:
        with urllib.request.urlopen(call, timeout=timeout) as response:
            body = _read_body(response, deadline, clock, method, path, timeout)
            return Reply(response.status, _lower(response.headers.items()), body)
    except urllib.error.HTTPError as error:
        with error:
            try:
                body = _read_body(error, deadline, clock, method, path, timeout)
            except OSError as read_error:
                if clock is not None:
                    clock.check()
                raise smoke.SmokeError(
                    f"{method} {path} got no answer: {read_error}"
                ) from read_error
            return Reply(error.code, _lower(error.headers.items()), body)
    except smoke.SmokeError:
        raise
    except OSError as error:
        if clock is not None:
            clock.check()  # the overall deadline, not this phase's timeout, may have run out
        raise smoke.SmokeError(f"{method} {path} got no answer: {error}") from error


def _read_body(
    stream: Any,
    deadline: float,
    clock: OverallClock | None,
    method: str,
    path: str,
    timeout: float,
) -> bytes:
    """Read to EOF, checking `deadline` after every chunk: `urlopen`'s socket timeout alone
    bounds only the gap between bytes, so a trickling reply would outlive it. Closing the
    caller's `with` lets the socket go; no thread is left behind."""
    read_chunk = getattr(stream, "read1", None)
    if read_chunk is None:  # a stand-in with only read(): it answers at once or not at all
        body: bytes = stream.read()
        if time.monotonic() > deadline:
            _overdue(clock, method, path, timeout)
        return body
    chunks: list[bytes] = []
    while chunk := read_chunk(65536):
        chunks.append(chunk)
        if time.monotonic() > deadline:
            _overdue(clock, method, path, timeout)
    return b"".join(chunks)


def _overdue(clock: OverallClock | None, method: str, path: str, timeout: float) -> None:
    if clock is not None:
        clock.check()  # raises, naming the phase, when the overall deadline was the binding one
    raise smoke.SmokeError(f"{method} {path} did not finish within {timeout:g} s")


def _lower(items: Any) -> dict[str, str]:
    return {str(name).lower(): str(value) for name, value in items}


def expect(reply: Reply, what: str, status: int = 200, **headers: str) -> Reply:
    """The reply must have this status and these headers (`content_type` for Content-Type, an
    exact match unless the value ends in `*`, a prefix)."""
    if reply.status != status:
        raise smoke.SmokeError(f"{what} answered {reply.status}, not {status}: {reply.text[:200]}")
    for name, want in headers.items():
        key = name.replace("_", "-")
        got = reply.headers.get(key, "")
        if not (got.startswith(want[:-1]) if want.endswith("*") else got == want):
            raise smoke.SmokeError(f"{what} has {key}: {got!r}, not {want!r}")
    return reply


def asset_refs(html: str) -> list[str]:
    """The `/assets/...` files an index.html names, in order, without repeats."""
    return list(dict.fromkeys(ASSET.findall(html)))


class EventStream:
    """GET /events on a thread of its own, each envelope validated against `ServerEvent`.

    A read that times out poisons a socket's file object, so the waiting is done on a queue with a
    deadline, and the thread only reads. `seen` is every event so far, from seq 1.
    """

    def __init__(self, base: str, clock: OverallClock | None = None) -> None:
        host, _, port = base.removeprefix("http://").partition(":")
        self._clock = clock
        opened = STREAM_IDLE if clock is None else clock.bounded(STREAM_IDLE)
        self._connection = http.client.HTTPConnection(host, int(port), timeout=opened)
        self._queue: queue.Queue[Any] = queue.Queue()
        self.seen: list[Any] = []
        try:
            self._connection.request("GET", "/events", headers={"Accept": "text/event-stream"})
            self._response = self._connection.getresponse()
        except (OSError, http.client.HTTPException) as error:
            if clock is not None:
                clock.check()
            raise smoke.SmokeError(f"GET /events got no answer: {error}") from error
        if clock is not None:
            clock.check()
        if self._response.status != 200:
            raise smoke.SmokeError(f"GET /events answered {self._response.status}, not 200")
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        data: list[str] = []
        try:
            for raw in self._response:
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith("data:"):
                    data.append(line.removeprefix("data:").removeprefix(" "))
                elif not line and data:
                    self._queue.put(json.loads("\n".join(data)))
                    data = []
        except Exception as error:  # a thread's end: whatever broke the stream goes to the waiter
            self._queue.put(error)
        self._queue.put(None)  # the stream ended

    def wait(
        self,
        match: Callable[[Any], bool],
        deadline_s: float,
        what: str,
        *,
        after_seq: int,
        agent_id: str | None = None,
        change_id: str | None = None,
        revision: int | None = None,
    ) -> Any:
        """Match strictly after a command's server seq, including queued or already read events.

        Correlation filters apply before the predicate; an earlier revision or another agent can
        never satisfy a wait. `after_seq` is required, so callers cannot silently reuse history.
        """
        clock = self._clock
        end = time.monotonic() + (deadline_s if clock is None else clock.bounded(deadline_s))
        index = bisect_right(self.seen, after_seq, key=lambda e: e.seq)
        while True:
            while index < len(self.seen):
                event = self.seen[index]
                index += 1
                if (
                    event.seq > after_seq
                    and (agent_id is None or getattr(event.payload, "agent_id", None) == agent_id)
                    and (
                        change_id is None or getattr(event.payload, "change_id", None) == change_id
                    )
                    and (revision is None or getattr(event.payload, "revision", None) == revision)
                    and match(event)
                ):
                    return event
            try:
                item = self._queue.get(timeout=max(end - time.monotonic(), 0.0))
            except queue.Empty:
                if clock is not None:
                    clock.check()  # raises, naming the phase, if the overall deadline ran out
                raise smoke.SmokeError(f"no {what} on /events within {deadline_s:g} s") from None
            if item is None or isinstance(item, Exception):
                raise smoke.SmokeError(f"/events ended before {what}: {item or 'closed'}")
            try:
                event = EVENTS.validate_python(item)
            except ValidationError as error:
                raise smoke.SmokeError(
                    f"an event does not validate: {error.errors()[0]}"
                ) from error
            expected_seq = self.seen[-1].seq + 1 if self.seen else 1
            if event.seq != expected_seq:
                raise smoke.SmokeError(f"/events seq {event.seq}, expected {expected_seq}")
            self.seen.append(event)
            if event.type == "error":
                raise smoke.SmokeError(
                    f"an error event came before {what}: {event.payload.message}"
                )

    def close(self) -> None:
        self._connection.close()
