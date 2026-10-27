"""`make demo-check`: the demo's one-command path, three times in a row, from a clean start.

AGENTS.md: a level is done when its demo runs three times in a row from a clean start, with one
command and no manual fixes. `make demo` builds the frontend and runs `python -m oversight serve`,
FastAPI serving the build. This check builds once (the Makefile does, before it), then runs that
server three times on the scripted fake model, so it spends no credits and needs no key: the
provider keys are blanked in the child's environment, which also hides any value in .env.

Each run, on a free port and in one runtime directory shared by all three (so a run starts over
what the one before left, as `make demo` does):
  1. start the server and wait for /health and /graph;
  2. GET / is the built index.html and names the built assets; each of them is served with its
     long cache header; a client route falls back to index.html; an unknown API path is the API's
     404, not the UI;
  3. the graph is the same as the first run's: the workspace really started clean;
  4. POST /agents with the fake script's task, then on /events wait for change.proposed;
  5. POST approve, then wait for change.resolved (merged) and graph.delta (cause merge);
  6. stop the server (SIGTERM, with STOP_DEADLINE to exit before SIGKILL), then check it exited 0,
     no process of its tree is left, and the runtime lock is free.
The server runs in a session of its own, and this check is a child subreaper (prctl): whatever the
server's tree leaves behind, in its session or out of it (a child that started a session of its own
and was orphaned long before the end), is adopted by this check, so it is found after the stop.
The check ends a leftover by PID, never by name. Exit 0 when all runs pass; 1 with a one-line
reason on stderr otherwise.

The hard bound is not in here: it is the Makefile's outer `timeout --kill-after`. The deadlines
in this file are best effort against a pathological server: in a normal failure they name the
phase that ran out, but no in-process deadline can promise to fire. The server is therefore set
to take SIGTERM when this check's process dies (PR_SET_PDEATHSIG in the child before exec), so
even a SIGKILL of this check alone takes the server down through its own SIGTERM shutdown; the
server's own children end on that shutdown, and `run_tests` has its own timeout. Only the outer
timeout, plus that parent-death signal, promises that no process is left behind.
"""

import contextlib
import ctypes
import fcntl
import http.client
import json
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

import smoke
from oversight_contracts.commands import AgentCreated, GraphResponse
from oversight_contracts.events import ServerEvent

REPO_ROOT = smoke.REPO_ROOT
DIST = REPO_ROOT / "frontend" / "dist"
FAKE_TASK = json.loads((REPO_ROOT / smoke.FAKE_SCRIPT).read_text(encoding="utf-8"))["task"]
RUNS = 3
REQUEST_TIMEOUT = 10.0  # seconds one HTTP request may take
START_DEADLINE = 30.0  # seconds /health and /graph have to answer within
# POST /agents answers only once the demo venv is built, which the first run does: up to the
# server's own bounds for it (a 60 s probe, 120 s to create the venv, 300 s to install pytest).
START_AGENT_DEADLINE = 480.0
PROPOSE_DEADLINE = 240.0  # the agent's run: from the answer to POST /agents to change.proposed
DECIDE_DEADLINE = 60.0  # approve to change.resolved and graph.delta
STOP_DEADLINE = 20.0  # seconds the server gets after SIGTERM before SIGKILL
# Plain requests in one run (GET /, its assets, the fallback, an API 404, GET /graph, POST
# approve, plus headroom for more assets), each allowed REQUEST_TIMEOUT.
REQUESTS_PER_RUN = 10
OVERALL_MARGIN = 60.0  # slack over the worst-case sum, for process start and cleanup
# Worst case: every phase in every run takes its full deadline. START_AGENT_DEADLINE (the cold
# 480 s venv build, once in practice but allowed per run here) and PROPOSE_DEADLINE drive it.
PER_RUN_DEADLINE = (
    2 * START_DEADLINE
    + START_AGENT_DEADLINE
    + PROPOSE_DEADLINE
    + 2 * DECIDE_DEADLINE
    + 2 * STOP_DEADLINE  # SIGTERM wait, then the SIGKILL wait in smoke.stop_child
    + REQUESTS_PER_RUN * REQUEST_TIMEOUT
)
OVERALL_DEADLINE = RUNS * PER_RUN_DEADLINE + OVERALL_MARGIN
GRACEFUL_EXITS = (0, -signal.SIGTERM)  # uvicorn ends a SIGTERM stop by raising the signal again
STREAM_IDLE = 120.0  # seconds a read on /events may wait: the server pings every 15
PR_SET_PDEATHSIG = 1  # linux/prctl.h
PR_SET_CHILD_SUBREAPER = 36  # linux/prctl.h
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
    timeout: float = REQUEST_TIMEOUT,
    clock: OverallClock | None = None,
) -> Reply:
    """GET, or POST with a JSON body when `post` is given. An error status is a reply, not an
    exception; no answer at all within `timeout` seconds is SmokeError. The timeout bounds the
    reply in total, not just idleness between bytes, and never outlives `clock`."""
    method = "GET" if post is None else "POST"
    data = None if post is None else json.dumps(post).encode("utf-8")
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


def check_ui(base: str, clock: OverallClock | None = None) -> None:
    """The UI is served as `make demo` promises: index.html, its assets, the SPA fallback, and an
    API path that is never the UI."""
    built = (DIST / "index.html").read_bytes()
    index = expect(
        request(base, "/", clock=clock),
        "GET /",
        content_type="text/html*",
        cache_control="no-cache",
    )
    if index.body != built:
        raise smoke.SmokeError("GET / is not frontend/dist/index.html")
    refs = asset_refs(built.decode("utf-8"))
    if not any(ref.endswith(".js") for ref in refs) or not any(
        ref.endswith(".css") for ref in refs
    ):
        raise smoke.SmokeError("frontend/dist/index.html names no built script and style sheet")
    for ref in refs:
        asset = expect(
            request(base, ref, clock=clock),
            f"GET {ref}",
            cache_control="public, max-age=31536000, immutable",
        )
        if asset.body != (DIST / ref.lstrip("/")).read_bytes():
            raise smoke.SmokeError(f"GET {ref} is not the built file")
    route = expect(request(base, "/review/change-1", clock=clock), "GET /review/change-1")
    if route.body != built:
        raise smoke.SmokeError("a client route does not fall back to index.html")
    unknown = expect(
        request(base, "/graph/nope", clock=clock),
        "GET /graph/nope",
        404,
        content_type="application/json*",
    )
    if b"<!doctype" in unknown.body.lower():
        raise smoke.SmokeError("an unknown API path was answered with index.html")


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

    def wait(self, match: Callable[[Any], bool], deadline_s: float, what: str) -> Any:
        """The first event `match` accepts, among those already seen and then those that arrive,
        within the deadline, and within the overall deadline when this stream has a clock. An
        `error` event, or the stream ending, ends the wait at once."""
        clock = self._clock
        end = time.monotonic() + (deadline_s if clock is None else clock.bounded(deadline_s))
        index = 0
        while True:
            while index < len(self.seen):
                event = self.seen[index]
                index += 1
                if match(event):
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
            self.seen.append(event)
            if event.type == "error":
                raise smoke.SmokeError(
                    f"an error event came before {what}: {event.payload.message}"
                )

    def close(self) -> None:
        self._connection.close()


def proc_table() -> dict[int, tuple[str, int, int, int]]:
    """Every process now: pid -> (state, parent, process group, session), from /proc."""
    table: dict[int, tuple[str, int, int, int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            text = (entry / "stat").read_text(encoding="utf-8")
        except OSError:  # gone since the listing
            continue
        # The name sits in parentheses and may hold spaces: the fields start after the last ")".
        state, parent, group, session = text[text.rindex(")") + 2 :].split()[:4]
        table[int(entry.name)] = (state, int(parent), int(group), int(session))
    return table


def descendants(root: int, table: dict[int, tuple[str, int, int, int]]) -> set[int]:
    """The processes below `root` in `table`: its children, theirs, and so on."""
    found: set[int] = set()
    frontier = [root]
    while frontier:
        parent = frontier.pop()
        for pid, (_, ppid, _, _) in table.items():
            if ppid == parent and pid not in found:
                found.add(pid)
                frontier.append(pid)
    return found


@contextlib.contextmanager
def adopting_orphans() -> Generator[None]:
    """Make this process a child subreaper for the block: a process whose parent dies is adopted by
    it, not by init, wherever its session went. What the server's tree leaves behind is then below
    this process when the server is gone, and `stragglers` finds it."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, ctypes.c_ulong(1), 0, 0, 0) != 0:
        error = os.strerror(ctypes.get_errno())
        raise smoke.SmokeError(f"this check cannot become a child subreaper: {error}")
    try:
        yield
    finally:
        libc.prctl(PR_SET_CHILD_SUBREAPER, ctypes.c_ulong(0), 0, 0, 0)


def stragglers(session: int, reaper: int | None = None) -> set[int]:
    """Live processes of the server's session or process group, and every live process below
    `reaper`, the subreaper that adopts what the server's tree left (see `adopting_orphans`). A
    zombie is dead, only not reaped yet."""
    table = proc_table()
    adopted: set[int] = descendants(reaper, table) if reaper is not None else set()
    return {
        pid
        for pid, (state, _, group, sid) in table.items()
        if state != "Z" and (sid == session or group == session or pid in adopted)
    }


def lock_is_free(runtime_dir: Path) -> bool:
    """True when nobody holds the runtime lock: taking it works, and is let go at once."""
    try:
        descriptor = os.open(runtime_dir / "server.lock", os.O_RDWR)
    except OSError as error:
        raise smoke.SmokeError(f"the runtime lock cannot be opened: {error}") from error
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(descriptor)


def server_env(port: int, runtime_dir: Path) -> dict[str, str]:
    """The smoke test's child environment, on the fake model, with the provider keys set to empty:
    an empty variable beats a value in .env, so no key reaches the child from anywhere."""
    return smoke.child_env(port, runtime_dir) | {"META_API_KEY": "", "BASETEN_API_KEY": ""}


def start_agent(base: str, clock: OverallClock | None = None) -> AgentCreated:
    """POST /agents with the fake script's task. The server answers only after the demo venv is
    built, which the first run waits for, so the request has START_AGENT_DEADLINE."""
    reply = expect(
        request(
            base, "/agents", post={"task": FAKE_TASK}, timeout=START_AGENT_DEADLINE, clock=clock
        ),
        "POST /agents",
        201,
    )
    return AgentCreated.model_validate(json.loads(reply.body))


def _die_with_parent(expected_parent: int) -> None:
    """In the server child, before exec: take SIGTERM when this check's process dies, for any
    reason including SIGKILL, so the Makefile's outer timeout leaves no server behind. The
    server's SIGTERM shutdown then ends its own children; `run_tests` has its own timeout.
    `expected_parent` is this check's PID, captured before the fork: if it is no longer the
    parent after prctl, the check died in between, no death signal will ever come, so exit."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_PDEATHSIG, ctypes.c_ulong(signal.SIGTERM), 0, 0, 0) != 0:
        raise OSError(os.strerror(ctypes.get_errno()))
    if os.getppid() != expected_parent:
        os._exit(1)


def start_server(port: int, runtime_dir: Path, output: Any) -> subprocess.Popen[Any]:
    """The server for one run, in a session of its own and set to die with this check."""
    parent_pid = os.getpid()  # before the fork, for the child's post-prctl parent check
    return subprocess.Popen(
        [sys.executable, "-m", "oversight", *smoke.BACKEND_ARGS],
        cwd=REPO_ROOT,
        env=server_env(port, runtime_dir),
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # its own session: most of what is left is found by session
        preexec_fn=lambda: _die_with_parent(parent_pid),
    )


def stop_server(child: subprocess.Popen[Any], clock: OverallClock | None = None) -> None:
    """SIGTERM, then SIGKILL only after STOP_DEADLINE: a slow but valid shutdown is not killed at
    smoke's shorter deadline. With a clock, the two waits together take at most what the overall
    deadline has left (an expired one stops the server at once instead of waiting 40 s more)."""
    if clock is None:
        smoke.stop_child(child, STOP_DEADLINE)
        return
    budget = max(min(2 * STOP_DEADLINE, clock.remaining()), 0.0)
    end = time.monotonic() + budget
    if child.poll() is None:
        child.terminate()
    try:
        child.wait(timeout=min(STOP_DEADLINE, budget))
    except subprocess.TimeoutExpired:
        child.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            child.wait(timeout=max(end - time.monotonic(), 0.0))


def graph_of(base: str, clock: OverallClock | None = None) -> GraphResponse:
    timeout = REQUEST_TIMEOUT if clock is None else clock.bounded(REQUEST_TIMEOUT)
    status, body = smoke.fetch_json(f"{base}/graph", time.monotonic() + timeout)
    if status != 200:
        if clock is not None:
            clock.check()
        raise smoke.SmokeError(f"GET /graph answered {status}, not 200")
    try:
        return GraphResponse.model_validate(body)
    except ValidationError as error:
        raise smoke.SmokeError(f"/graph does not validate: {error.errors()[0]['msg']}") from error


def serve_and_decide(
    base: str, first: GraphResponse | None, clock: OverallClock, run: int
) -> GraphResponse:
    """Steps 1 to 5 against a server that is up: the UI, the clean graph, one approved change."""
    clock.set_phase(f"run {run}: wait for /graph")
    try:
        smoke.wait_for_graph(base, clock.bounded(START_DEADLINE))
    except smoke.SmokeError:
        clock.check()  # raises, naming this phase, if the overall deadline was the binding one
        raise
    clock.set_phase(f"run {run}: check the UI")
    check_ui(base, clock)
    clock.set_phase(f"run {run}: GET /graph")
    graph = graph_of(base, clock)
    if first is not None and (graph.graph.version, graph.graph.commit) != (
        first.graph.version,
        first.graph.commit,
    ):
        raise smoke.SmokeError(
            f"the graph is {graph.graph.version} at {graph.graph.commit[:8]}, not the first "
            f"run's {first.graph.version} at {first.graph.commit[:8]}: not a clean start"
        )
    stream = EventStream(base, clock=clock)
    try:
        clock.set_phase(f"run {run}: POST /agents (venv build)")
        mine = start_agent(base, clock).change_id
        clock.set_phase(f"run {run}: wait for change.proposed")
        stream.wait(
            lambda e: e.type == "change.proposed" and e.payload.change_id == mine,
            PROPOSE_DEADLINE,
            "change.proposed",
        )
        clock.set_phase(f"run {run}: POST approve")
        expect(request(base, f"/changes/{mine}/approve", post={}, clock=clock), "POST approve", 202)
        clock.set_phase(f"run {run}: wait for change.resolved")
        resolved = stream.wait(
            lambda e: e.type == "change.resolved" and e.payload.change_id == mine,
            DECIDE_DEADLINE,
            "change.resolved",
        )
        if resolved.payload.merge != "merged":
            raise smoke.SmokeError(f"the change was {resolved.payload.merge}, not merged")
        clock.set_phase(f"run {run}: wait for graph.delta")
        delta = stream.wait(
            lambda e: e.type == "graph.delta" and e.payload.cause == "merge",
            DECIDE_DEADLINE,
            "graph.delta",
        )
        if delta.payload.from_version != graph.graph.version:
            raise smoke.SmokeError("the merge's graph.delta does not start at the clean graph")
    finally:
        stream.close()
    return first if first is not None else graph


def run_once(
    number: int,
    scratch: Path,
    runtime_dir: Path,
    first: GraphResponse | None,
    clock: OverallClock,
) -> GraphResponse:
    """One full run; the first run's graph, which the later runs must match."""
    port = smoke.free_port()
    base = smoke.base_url(port)
    log = scratch / f"server-{number}.log"
    with log.open("wb") as output:
        child = start_server(port, runtime_dir, output)
    try:
        clock.set_phase(f"run {number}: wait for /health")
        try:
            smoke.wait_for_health(base, clock.bounded(START_DEADLINE), child)
        except smoke.SmokeError:
            clock.check()  # raises, naming this phase, if the overall deadline bound the wait
            raise
        graph = serve_and_decide(base, first, clock, number)
    except smoke.SmokeError as error:
        raise smoke.SmokeError(f"run {number}: {error}\n{_tail(log)}") from error
    finally:
        with contextlib.suppress(smoke.SmokeError):
            # Checks before renaming: an expiry belongs to the phase before, cleanup still runs.
            clock.set_phase(f"run {number}: stop the server")
        stop_server(child, clock)
        left = stragglers(child.pid, os.getpid())
        for pid in left:  # ours: the server's tree, started by this check, or adopted by it
            _kill(pid)
    clock.check()
    if child.returncode not in GRACEFUL_EXITS:
        raise smoke.SmokeError(f"run {number}: the server exited {child.returncode}\n{_tail(log)}")
    if left:
        raise smoke.SmokeError(f"run {number}: processes left after the server stopped: {left}")
    if not lock_is_free(runtime_dir):
        raise smoke.SmokeError(f"run {number}: the runtime lock is still held after the stop")
    print(f"demo-check: run {number}: ok on port {port}")
    return graph


def _kill(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


def _tail(log: Path, lines: int = 25) -> str:
    text = log.read_text(encoding="utf-8", errors="replace").splitlines()
    return "server log, last lines:\n" + "\n".join(text[-lines:])


def _on_sigterm(_signal: int, _frame: object) -> None:
    raise KeyboardInterrupt  # `timeout` ends this check with SIGTERM: the finally blocks still run


def run_checks(runs: int = RUNS) -> None:
    if not (DIST / "index.html").is_file():
        raise smoke.SmokeError(
            "frontend/dist/index.html is missing: build it first (make demo-check)"
        )
    first: GraphResponse | None = None
    clock = OverallClock(deadline_s=runs * PER_RUN_DEADLINE + OVERALL_MARGIN)
    with tempfile.TemporaryDirectory(prefix="oversight-demo-check-") as scratch, adopting_orphans():
        # One runtime directory for every run, so each run starts over what the last one left. The
        # workspace refuses a directory with another name.
        runtime_dir = Path(os.path.realpath(scratch)) / ".runtime"
        for number in range(1, runs + 1):
            first = run_once(number, Path(scratch), runtime_dir, first, clock)
        clock.set_phase("cleanup")
    print(f"demo-check: ok: {runs} runs from a clean start, no credits")


def main() -> int:
    if sys.argv[1:] == ["--print-deadline"]:
        print(int(OVERALL_DEADLINE))
        return 0
    signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        run_checks()
    except smoke.SmokeError as error:
        print(f"demo-check: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("demo-check: interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
