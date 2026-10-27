"""Real make dev/replay launchers, isolated ports/state, fakes only; run by make test-browser."""

import contextlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from pydantic import TypeAdapter

from oversight_contracts.commands import ErrorResponse, GraphResponse
from oversight_contracts.events import ServerEvent

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def distinct_ports() -> tuple[int, int]:
    p = free_port()
    v = free_port()
    while v == p:
        v = free_port()
    return p, v


def stop(child: subprocess.Popen) -> None:
    if child.poll() is None:
        child.terminate()
    try:
        child.wait(timeout=15)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def signal_pid(path: Path, sig: signal.Signals = signal.SIGTERM) -> None:
    if path.exists():
        with contextlib.suppress(ProcessLookupError):
            os.kill(int(path.read_text()), sig)


def write_shims(tmp_path: Path, *, capture: bool) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = shlex.quote(shutil.which("uv") or "uv")
    python = shlex.quote(sys.executable)
    # Resolve the real ports even in capture mode; no settings file is replaced or sourced.
    uv_script = f"""#!/bin/bash
set -eu
if [ "${{4:-}}" = "tools/resolve_ports.py" ]; then
  exec {python} tools/resolve_ports.py
fi
printf '%s %s\\n' "$OVERSIGHT_PORT" "$OVERSIGHT_VITE_PORT" > "$FIXTURE/backend.ports"
echo $$ > "$FIXTURE/backend.pid"
"""
    if capture:
        uv_script += "trap 'exit 0' TERM\nwhile true; do sleep 0.05; done\n"
    else:
        uv_script += f'exec {uv} "$@"\n'
    vite = shlex.quote(str(ROOT / "frontend/node_modules/vite/bin/vite.js"))
    pnpm_script = """#!/bin/bash
set -eu
printf '%s %s\\n' "$OVERSIGHT_PORT" "$OVERSIGHT_VITE_PORT" > "$FIXTURE/vite.ports"
echo $$ > "$FIXTURE/vite.pid"
"""
    if capture:
        pnpm_script += """for ((i=0; i<200; i++)); do
  [ -s "$FIXTURE/backend.pid" ] && exit 0
  sleep 0.05
done
exit 1
"""
    else:
        pnpm_script += f"cd frontend\nexec node {vite} --configLoader runner\n"
    for name, script in (("uv", uv_script), ("pnpm", pnpm_script)):
        (bin_dir / name).write_text(script)
        (bin_dir / name).chmod(0o755)


@contextlib.contextmanager
def launcher(
    tmp_path: Path, target: str, env_text: str, overrides: dict[str, str], *, capture: bool = False
) -> Iterator[tuple[subprocess.Popen, Path]]:
    """The actual Makefile in a scratch cwd: port-only .env, independent runtime and Vite cache.

    Shims record the launcher's resolved exports. Integration shims exec the real processes;
    capture-only shims never bind default ports. Stopping Vite lets make reap its backend.
    """
    (tmp_path / ".env").write_text(env_text)
    (tmp_path / "tools").symlink_to(ROOT / "tools", target_is_directory=True)
    (tmp_path / "frontend").symlink_to(ROOT / "frontend", target_is_directory=True)
    (tmp_path / "fixtures").symlink_to(ROOT / "fixtures", target_is_directory=True)
    write_shims(tmp_path, capture=capture)
    bin_dir = tmp_path / "bin"
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("OVERSIGHT_", "META_", "BASETEN_", "GIT_", "MAKE", "MFLAGS"))
    }
    env.update(
        {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FIXTURE": str(tmp_path),
            "UV_PROJECT": str(ROOT),
            "UV_OFFLINE": "1",
            "OVERSIGHT_HOST": "127.0.0.1",
            "OVERSIGHT_RUNTIME_DIR": str(tmp_path / ".runtime"),
            "OVERSIGHT_VITE_CACHE_DIR": str(tmp_path / "vite-cache"),
            "OVERSIGHT_AGENT_MODEL": "fake",
            "OVERSIGHT_AGENT_MODEL_B": "fake",
            "OVERSIGHT_FAKE_SCRIPT": "fixtures/fake-model/document-apply-coupon.json",
            "OVERSIGHT_FAKE_SCRIPT_B": "fixtures/fake-model/document-restock.json",
        }
    )
    env.update(overrides)
    log = tmp_path / "launcher.log"
    with log.open("w") as output:
        child = subprocess.Popen(
            ["make", "--no-print-directory", "-f", str(ROOT / "Makefile"), target, "SPEED=0"],
            cwd=tmp_path,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    try:
        yield child, log
    finally:
        signal_pid(tmp_path / "vite.pid")
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            stop(child)
        signal_pid(tmp_path / "backend.pid")


@pytest.mark.parametrize("env_text", ["", "OVERSIGHT_VITE_PORT=\n"])
@pytest.mark.parametrize("target", ["dev", "replay"])
def test_default_exports_without_binding_default_ports(
    tmp_path: Path, target: str, env_text: str
) -> None:
    with launcher(tmp_path, target, env_text, {}, capture=True) as (child, log):
        assert child.wait(timeout=20) == 0, log.read_text()
    assert (tmp_path / "backend.ports").read_text() == "8710 5710\n"
    assert (tmp_path / "vite.ports").read_text() == "8710 5710\n"


@pytest.mark.parametrize("target", ["dev", "replay"])
@pytest.mark.parametrize("environment_override", [False, True])
def test_actual_proxy_graph_stream_and_resume(
    tmp_path: Path, target: str, environment_override: bool
) -> None:
    port, vite_port = distinct_ports()
    file_ports = distinct_ports() if environment_override else (port, vite_port)
    text = f"OVERSIGHT_PORT={file_ports[0]}\nOVERSIGHT_VITE_PORT={file_ports[1]}\n"
    overrides = (
        {"OVERSIGHT_PORT": str(port), "OVERSIGHT_VITE_PORT": str(vite_port)}
        if environment_override
        else {}
    )
    with launcher(tmp_path, target, text, overrides) as (child, log):
        base = f"http://127.0.0.1:{vite_port}"
        with httpx.Client(base_url=base, timeout=5, trust_env=False) as client:
            deadline = time.monotonic() + 60
            while True:
                assert child.poll() is None, log.read_text()
                try:
                    graph = client.get("/graph")
                    if graph.status_code == 200:
                        break
                    assert graph.status_code in (404, 500, 502, 503), log.read_text() + graph.text
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, log.read_text()
                time.sleep(0.1)
            assert GraphResponse.model_validate(graph.json()).graph.nodes
            # Browser-supplied authority and Origin reach the backend intact through Vite.
            assert client.get("/graph", headers={"Origin": base}).status_code == 200
            wrong_host = client.get("/graph", headers={"Host": "127.0.0.1:1"})
            assert wrong_host.status_code == 403
            assert ErrorResponse.model_validate(wrong_host.json()).error.code == "forbidden"
            denied = client.post(
                "/agents", headers={"Origin": "http://evil.test"}, json={"task": "never run"}
            )
            assert denied.status_code == 403
            assert ErrorResponse.model_validate(denied.json()).error.code == "forbidden"
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5
            ) as direct:
                assert (
                    direct.put("/files", headers={"Host": f"evil.test:{port}"}).status_code == 403
                )
            # Both connections remain open: reading an event proves streaming, not a buffered body.
            adapter = TypeAdapter(ServerEvent)
            seq = 0
            for attempt in range(2):
                headers = (
                    {"Origin": base, "Last-Event-ID": str(seq)} if attempt else {"Origin": base}
                )
                with client.stream("GET", "/events", headers=headers) as response:
                    assert response.status_code == 200
                    assert response.headers["content-type"].startswith("text/event-stream")
                    assert "content-encoding" not in response.headers
                    for line in response.iter_lines():
                        if line.startswith("data: "):
                            event = adapter.validate_python(json.loads(line[6:]))
                            assert event.seq > seq
                            seq = event.seq
                            break
                    else:
                        pytest.fail("stream ended without an event")
        expected = f"{port} {vite_port}\n"
        assert (tmp_path / "backend.ports").read_text() == expected
        assert (tmp_path / "vite.ports").read_text() == expected


@pytest.mark.parametrize("target", ["dev", "replay"])
@pytest.mark.parametrize(
    "env_text",
    [
        "OVERSIGHT_PORT=0\n",
        "OVERSIGHT_VITE_PORT=65536\n",
        "OVERSIGHT_PORT=9001\nOVERSIGHT_VITE_PORT=9001\n",
    ],
)
def test_invalid_ports_stop_launcher_before_either_process(tmp_path, target, env_text) -> None:
    with launcher(tmp_path, target, env_text, {}, capture=True) as (child, log):
        assert child.wait(timeout=20) != 0
        assert "OVERSIGHT_" in log.read_text()
    assert not (tmp_path / "backend.pid").exists()
    assert not (tmp_path / "vite.pid").exists()
