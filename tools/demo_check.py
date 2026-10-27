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
  6. stop the server (SIGTERM), then check it exited 0, no process is left in its session, and the
     runtime lock is free.
The server runs in a session of its own, so a leftover is found by session, and the check ends it by
PID, never by name. Exit 0 when all runs pass; 1 with a one-line reason on stderr otherwise.
"""

import contextlib
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
from collections.abc import Callable
from dataclasses import dataclass
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
PROPOSE_DEADLINE = 240.0  # POST /agents to change.proposed: the first run builds the demo venv
DECIDE_DEADLINE = 60.0  # approve to change.resolved and graph.delta
STOP_DEADLINE = 20.0  # seconds the server gets after SIGTERM before SIGKILL
GRACEFUL_EXITS = (0, -signal.SIGTERM)  # uvicorn ends a SIGTERM stop by raising the signal again
STREAM_IDLE = 120.0  # seconds a read on /events may wait: the server pings every 15
ASSET = re.compile(r'(?:src|href)="(/assets/[^"]+)"')
EVENTS: TypeAdapter[Any] = TypeAdapter(ServerEvent)


@dataclass(frozen=True)
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def request(base: str, path: str, *, post: object | None = None) -> Reply:
    """GET, or POST with a JSON body when `post` is given. An error status is a reply, not an
    exception; no answer at all is SmokeError."""
    method = "GET" if post is None else "POST"
    data = None if post is None else json.dumps(post).encode("utf-8")
    headers = {} if data is None else {"Content-Type": "application/json"}
    call = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(call, timeout=REQUEST_TIMEOUT) as response:
            return Reply(response.status, _lower(response.headers.items()), response.read())
    except urllib.error.HTTPError as error:
        with error:
            return Reply(error.code, _lower(error.headers.items()), error.read())
    except OSError as error:
        raise smoke.SmokeError(f"{method} {path} got no answer: {error}") from error


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


def check_ui(base: str) -> None:
    """The UI is served as `make demo` promises: index.html, its assets, the SPA fallback, and an
    API path that is never the UI."""
    built = (DIST / "index.html").read_bytes()
    index = expect(request(base, "/"), "GET /", content_type="text/html*", cache_control="no-cache")
    if index.body != built:
        raise smoke.SmokeError("GET / is not frontend/dist/index.html")
    refs = asset_refs(built.decode("utf-8"))
    if not any(ref.endswith(".js") for ref in refs) or not any(
        ref.endswith(".css") for ref in refs
    ):
        raise smoke.SmokeError("frontend/dist/index.html names no built script and style sheet")
    for ref in refs:
        asset = expect(
            request(base, ref), f"GET {ref}", cache_control="public, max-age=31536000, immutable"
        )
        if asset.body != (DIST / ref.lstrip("/")).read_bytes():
            raise smoke.SmokeError(f"GET {ref} is not the built file")
    route = expect(request(base, "/review/change-1"), "GET /review/change-1")
    if route.body != built:
        raise smoke.SmokeError("a client route does not fall back to index.html")
    unknown = expect(
        request(base, "/graph/nope"), "GET /graph/nope", 404, content_type="application/json*"
    )
    if b"<!doctype" in unknown.body.lower():
        raise smoke.SmokeError("an unknown API path was answered with index.html")


class EventStream:
    """GET /events on a thread of its own, each envelope validated against `ServerEvent`.

    A read that times out poisons a socket's file object, so the waiting is done on a queue with a
    deadline, and the thread only reads. `seen` is every event so far, from seq 1.
    """

    def __init__(self, base: str) -> None:
        host, _, port = base.removeprefix("http://").partition(":")
        self._connection = http.client.HTTPConnection(host, int(port), timeout=STREAM_IDLE)
        self._queue: queue.Queue[Any] = queue.Queue()
        self.seen: list[Any] = []
        try:
            self._connection.request("GET", "/events", headers={"Accept": "text/event-stream"})
            self._response = self._connection.getresponse()
        except (OSError, http.client.HTTPException) as error:
            raise smoke.SmokeError(f"GET /events got no answer: {error}") from error
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
        within the deadline. An `error` event, or the stream ending, ends the wait at once."""
        end = time.monotonic() + deadline_s
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


def descendants(root: int) -> set[int]:
    """The processes below `root` now: its children, theirs, and so on."""
    table = proc_table()
    found: set[int] = set()
    frontier = [root]
    while frontier:
        parent = frontier.pop()
        for pid, (_, ppid, _, _) in table.items():
            if ppid == parent and pid not in found:
                found.add(pid)
                frontier.append(pid)
    return found


def stragglers(session: int, earlier: set[int]) -> set[int]:
    """Live processes of the server's session or process group, and any of `earlier` (its
    descendants seen while it ran) that is still alive. A zombie is dead, only not reaped yet."""
    return {
        pid
        for pid, (state, _, group, sid) in proc_table().items()
        if state != "Z" and (sid == session or group == session or pid in earlier)
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


def graph_of(base: str) -> GraphResponse:
    status, body = smoke.fetch_json(f"{base}/graph", time.monotonic() + REQUEST_TIMEOUT)
    if status != 200:
        raise smoke.SmokeError(f"GET /graph answered {status}, not 200")
    try:
        return GraphResponse.model_validate(body)
    except ValidationError as error:
        raise smoke.SmokeError(f"/graph does not validate: {error.errors()[0]['msg']}") from error


def serve_and_decide(base: str, first: GraphResponse | None) -> GraphResponse:
    """Steps 1 to 5 against a server that is up: the UI, the clean graph, one approved change."""
    smoke.wait_for_graph(base, START_DEADLINE)
    check_ui(base)
    graph = graph_of(base)
    if first is not None and (graph.graph.version, graph.graph.commit) != (
        first.graph.version,
        first.graph.commit,
    ):
        raise smoke.SmokeError(
            f"the graph is {graph.graph.version} at {graph.graph.commit[:8]}, not the first "
            f"run's {first.graph.version} at {first.graph.commit[:8]}: not a clean start"
        )
    stream = EventStream(base)
    try:
        reply = expect(request(base, "/agents", post={"task": FAKE_TASK}), "POST /agents", 201)
        created = AgentCreated.model_validate(json.loads(reply.body))
        mine = created.change_id
        stream.wait(
            lambda e: e.type == "change.proposed" and e.payload.change_id == mine,
            PROPOSE_DEADLINE,
            "change.proposed",
        )
        expect(request(base, f"/changes/{mine}/approve", post={}), "POST approve", 202)
        resolved = stream.wait(
            lambda e: e.type == "change.resolved" and e.payload.change_id == mine,
            DECIDE_DEADLINE,
            "change.resolved",
        )
        if resolved.payload.merge != "merged":
            raise smoke.SmokeError(f"the change was {resolved.payload.merge}, not merged")
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
    number: int, scratch: Path, runtime_dir: Path, first: GraphResponse | None
) -> GraphResponse:
    """One full run; the first run's graph, which the later runs must match."""
    port = smoke.free_port()
    base = smoke.base_url(port)
    log = scratch / f"server-{number}.log"
    with log.open("wb") as output:
        child = subprocess.Popen(
            [sys.executable, "-m", "oversight", *smoke.BACKEND_ARGS],
            cwd=REPO_ROOT,
            env=server_env(port, runtime_dir),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # its own session: what is left of it is found by session
        )
    seen: set[int] = set()
    try:
        smoke.wait_for_health(base, START_DEADLINE, child)
        graph = serve_and_decide(base, first)
        seen = descendants(child.pid)
    except smoke.SmokeError as error:
        raise smoke.SmokeError(f"run {number}: {error}\n{_tail(log)}") from error
    finally:
        smoke.stop_child(child)
        left = stragglers(child.pid, seen)
        for pid in left:  # ours: the server's session, started by this check
            _kill(pid)
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
    with tempfile.TemporaryDirectory(prefix="oversight-demo-check-") as scratch:
        # One runtime directory for every run, so each run starts over what the last one left. The
        # workspace refuses a directory with another name.
        runtime_dir = Path(os.path.realpath(scratch)) / ".runtime"
        for number in range(1, runs + 1):
            first = run_once(number, Path(scratch), runtime_dir, first)
    print(f"demo-check: ok: {runs} runs from a clean start, no credits")


def main() -> int:
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
