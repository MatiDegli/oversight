"""The smoke test (docs/proposal.md, front 4): `make smoke`.

Starts the backend as a child process on a free port, with a runtime directory of its own inside a
temporary directory, so a running `make dev` or `make replay` is not disturbed. Waits for
GET /health to say ok, then checks GET /graph answers 200 and validates against GraphResponse,
with a seq. The child is stopped by PID on every exit path: success, failure, Ctrl+C. Exit 0 when
the backend starts and answers; 1 with a one-line reason on stderr otherwise.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from oversight_contracts.commands import GraphResponse

REPO_ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
# Today only replay mode mounts routes; the live `serve` mounts none until the API front wires
# them. This tuple is the one place the command lives, so that front switches it to ("serve",).
BACKEND_ARGS: tuple[str, ...] = (
    "replay",
    "fixtures/events/handwritten-level1.jsonl",
    "--speed",
    "0",
)
START_DEADLINE = 15.0  # seconds /health and then /graph have to answer within
REQUEST_TIMEOUT = 2.0  # seconds one HTTP attempt may take
POLL_INTERVAL = 0.05  # seconds between attempts
STOP_DEADLINE = 5.0  # seconds the child gets after SIGTERM before SIGKILL
MIN_SEQ = 1  # a snapshot event always has a seq, and seq starts at 1


class SmokeError(Exception):
    """The one-line reason `make smoke` fails with."""


def free_port() -> int:
    """A port the kernel reports free right now: bind to port 0, read it back, let it go."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


def base_url(port: int) -> str:
    return f"http://{HOST}:{port}"


def fetch_json(url: str) -> tuple[int | None, Any]:
    """GET the url once: (status, parsed body), (status, None) on an HTTP error the server
    answered with, and (None, None) when nothing could be read (refused, timed out, bad JSON)."""
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        error.close()
        return error.code, None
    except (OSError, ValueError):
        return None, None


def wait_for_health(
    base: str, deadline_seconds: float, child: subprocess.Popen[Any] | None = None
) -> Any:
    """Poll GET /health until it answers within the deadline; the parsed body when it does.

    Nothing answering is retried until the deadline, because the backend may still be starting.
    Any answer is final: uvicorn binds only after its startup, so 200 is ready and any other
    status stays that way.
    """
    deadline = time.monotonic() + deadline_seconds
    while True:
        if child is not None and child.poll() is not None:
            raise SmokeError(f"the backend exited with code {child.returncode} before answering")
        status, body = fetch_json(f"{base}/health")
        if status == 200:
            return body
        if status is not None:
            raise SmokeError(f"/health answered {status}, not 200")
        if time.monotonic() >= deadline:
            raise SmokeError(f"nothing answered /health within {deadline_seconds:g} s")
        time.sleep(POLL_INTERVAL)


def check_graph(body: Any) -> int:
    """Validate a /graph body against the contract model and return its seq."""
    try:
        response = GraphResponse.model_validate(body)
    except ValidationError as error:
        issue = error.errors()[0]
        field = ".".join(str(part) for part in issue["loc"])
        raise SmokeError(f"/graph does not validate: {field}: {issue['msg']}") from error
    if response.seq < MIN_SEQ:
        raise SmokeError(f"/graph reports seq {response.seq}, before the first event")
    return response.seq


def wait_for_graph(base: str, deadline_seconds: float) -> int:
    """GET /graph until it answers 200 within the deadline, then validate its body: the seq.

    A 404 right after startup is the replay player still publishing the snapshot, so that status
    is retried like no answer at all; any other wrong answer, and a body that fails validation,
    fail at once.
    """
    deadline = time.monotonic() + deadline_seconds
    while True:
        status, body = fetch_json(f"{base}/graph")
        if status == 200:
            return check_graph(body)
        if status is not None and status != 404:
            raise SmokeError(f"/graph answered {status}, not 200")
        if time.monotonic() >= deadline:
            if status == 404:
                raise SmokeError(f"/graph still had no snapshot after {deadline_seconds:g} s")
            raise SmokeError(f"/graph was not answered within {deadline_seconds:g} s")
        time.sleep(POLL_INTERVAL)


def stop_child(child: subprocess.Popen[Any]) -> None:
    """Stop the child by PID: SIGTERM, wait out the deadline, then SIGKILL. Never raises."""
    if child.poll() is None:
        child.terminate()
    try:
        child.wait(timeout=STOP_DEADLINE)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=STOP_DEADLINE)


def child_env(port: int, runtime_dir: Path) -> dict[str, str]:
    """The child's environment: a free port and a runtime directory of its own, in a temporary
    directory, so a running `make dev` or `make replay` on the usual port is not disturbed."""
    env = dict(os.environ)
    env.update(
        OVERSIGHT_HOST=HOST, OVERSIGHT_PORT=str(port), OVERSIGHT_RUNTIME_DIR=str(runtime_dir)
    )
    return env


def run_smoke() -> None:
    """One smoke run: start the backend, check it, stop it. Raises SmokeError with the reason."""
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="oversight-smoke-") as scratch:
        runtime_dir = Path(scratch) / "runtime"
        child = subprocess.Popen(
            [sys.executable, "-m", "oversight", *BACKEND_ARGS],
            cwd=REPO_ROOT,
            env=child_env(port, runtime_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            health = wait_for_health(base_url(port), START_DEADLINE, child)
            if health.get("ok") is not True:
                raise SmokeError("/health does not say ok")
            seq = wait_for_graph(base_url(port), START_DEADLINE)
        finally:
            # Every exit path: success, failure and Ctrl+C (the finally runs on the way out).
            stop_child(child)
    print(f"smoke: ok: /health and /graph on port {port}, seq {seq}")


def main() -> int:
    try:
        run_smoke()
    except SmokeError as error:
        print(f"smoke: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("smoke: interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
