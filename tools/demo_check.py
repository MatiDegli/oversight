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
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

from pydantic import ValidationError

import smoke
from demo_check_io import (
    DECIDE_DEADLINE as DECIDE_DEADLINE,
)
from demo_check_io import (
    OVERALL_DEADLINE as OVERALL_DEADLINE,
)
from demo_check_io import (
    OVERALL_MARGIN as OVERALL_MARGIN,
)
from demo_check_io import (
    PER_RUN_DEADLINE as PER_RUN_DEADLINE,
)
from demo_check_io import (
    PROPOSE_DEADLINE as PROPOSE_DEADLINE,
)
from demo_check_io import (
    REQUEST_TIMEOUT as REQUEST_TIMEOUT,
)
from demo_check_io import (
    REQUESTS_PER_RUN as REQUESTS_PER_RUN,
)
from demo_check_io import (
    RUNS as RUNS,
)
from demo_check_io import (
    START_AGENT_DEADLINE as START_AGENT_DEADLINE,
)
from demo_check_io import (
    START_DEADLINE as START_DEADLINE,
)
from demo_check_io import (
    STOP_DEADLINE as STOP_DEADLINE,
)
from demo_check_io import (
    EventStream as EventStream,
)
from demo_check_io import (
    OverallClock as OverallClock,
)
from demo_check_io import (
    Reply as Reply,
)
from demo_check_io import (
    asset_refs as asset_refs,
)
from demo_check_io import (
    expect as expect,
)
from demo_check_io import (
    request as request,
)
from oversight_contracts.commands import AgentCreated, GraphResponse

REPO_ROOT = smoke.REPO_ROOT
DIST = REPO_ROOT / "frontend" / "dist"
FAKE_TASK = json.loads((REPO_ROOT / smoke.FAKE_SCRIPT).read_text(encoding="utf-8"))["task"]
PR_SET_PDEATHSIG = 1  # linux/prctl.h
PR_SET_CHILD_SUBREAPER = 36  # linux/prctl.h
GRACEFUL_EXITS = (0, -signal.SIGTERM)


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
