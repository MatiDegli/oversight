"""Real-C acceptance primitives. No environment loading, credentials or open-runner fallback."""

import asyncio
import contextlib
import os
import socket
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI
from tools.preview_http_client import factory
from tools.preview_scenario import SCRIPT, Candidate, Events, Scenario, require

from oversight.app import create_app
from oversight.settings import REPO_ROOT, Settings
from oversight.workspace import IsolatedRunner, PreviewProfile, Workspace
from oversight.workspace.preview_process import PreviewProcess
from oversight.workspace.preview_registry import registered_identity


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def settings(
    root: Path, port: int, *, vite: int | None = None, pool: tuple[int, ...] = ()
) -> Settings:
    return Settings(
        runtime_dir=root,
        port=port,
        vite_port=vite,
        agent_model="fake",
        max_agents=1,
        fake_script=SCRIPT,
        demo_python=Path(sys.executable),
        memory_hold_percent=100,
        preview_profile=PreviewProfile(
            enabled=True,
            adapter=REPO_ROOT / "backend/src/oversight/preview_adapter.py",
            interpreter=Path(sys.executable),
            ports=pool or tuple({free_port() for _ in range(8)} - {port, vite})[:4],
            origins=(f"http://127.0.0.1:{port}",) + ((f"http://127.0.0.1:{vite}",) if vite else ()),
            trusted_demo=True,
            heartbeat_s=1,
            lease_s=30,
            lifetime_s=180,
            readiness_s=10,
        ),
    )


@dataclass
class AcceptanceServer:
    app: FastAPI
    client: httpx.AsyncClient
    origin: str
    witnesses: list["ProcessWitness"]


class ProcessWitness(PreviewProcess):
    """Observe the real supervisor; never substitute its launch, namespace or cleanup."""

    evidence: dict[str, Any] | None = None
    death_evidence: dict[str, Any] | None = None
    pins: contextlib.ExitStack

    async def ready(self, within_s: float) -> bool:
        result = await super().ready(within_s)
        if result:
            data = self.registry.read()
            assert data is not None
            self.pins.enter_context(pin_namespace(data))
            self.evidence = {
                "port": self.port,
                "namespaces": data["namespaces"],
                "births": {key: data[key] for key in ("supervisor", "unshare", "namespace")},
            }
            for key, value in data["namespaces"].items():
                assert os.readlink(f"/proc/{data['namespace']['pid']}/ns/{key}") == value
                assert value != os.readlink(f"/proc/self/ns/{key}")
        return result

    def verified(self) -> dict[str, Any]:
        if self.death_evidence is not None:
            return self.death_evidence
        assert self.dead and self.evidence is not None
        assert all(not registered_identity(raw).alive() for raw in self.evidence["births"].values())
        assert namespace_members(self.evidence["namespaces"]["pid"]) == []
        self.death_evidence = {**self.evidence, "namespace_dead": True}
        return self.death_evidence


@contextlib.contextmanager
def pin_namespace(data: dict[str, Any]):
    """Keep the authenticated namespace inode from being reused during a death assertion."""
    identity = registered_identity(data["namespace"])
    assert identity.alive()
    descriptor = os.open(f"/proc/{identity.pid}/ns/pid", os.O_RDONLY | os.O_CLOEXEC)
    try:
        assert os.readlink(f"/proc/self/fd/{descriptor}") == data["namespaces"]["pid"]
        assert identity.alive()
        yield descriptor
    finally:
        os.close(descriptor)


@asynccontextmanager
async def server(config: Settings) -> AsyncGenerator[AcceptanceServer]:
    config.runtime_dir.mkdir(parents=True, exist_ok=True)
    async with asyncio.timeout(15):
        await IsolatedRunner(config.runtime_dir).preflight()
    app = create_app(config)
    witnesses: list[ProcessWitness] = []
    pins = contextlib.ExitStack()

    def witness(registry: Any, port: int) -> ProcessWitness:
        process = ProcessWitness(registry, port)
        process.pins = pins
        witnesses.append(process)
        return process

    app.state.live.previews.process_factory = witness
    listener = socket.socket()
    listener.bind(("127.0.0.1", config.port))
    listener.listen(16)
    service = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="warning",
            access_log=False,
            proxy_headers=False,
            timeout_graceful_shutdown=2,
        )
    )
    task = asyncio.create_task(service.serve(sockets=[listener]))
    origin = f"http://127.0.0.1:{config.port}"
    try:
        async with httpx.AsyncClient(
            base_url=origin, timeout=5, trust_env=False, follow_redirects=False
        ) as client:
            async with asyncio.timeout(30):
                while not service.started:
                    if task.done():
                        await task
                        raise RuntimeError("Acceptance startup refused")
                    await asyncio.sleep(0.02)
            require(app.state.live.workspace.isolation.available, "Real C startup did not pass")
            yield AcceptanceServer(app, client, origin, witnesses)
    finally:
        service.should_exit = True
        try:
            async with asyncio.timeout(30):
                await asyncio.shield(task)
        finally:
            try:
                listener.close()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                for process in witnesses:
                    if process.evidence is not None:
                        process.verified()
            finally:
                pins.close()


async def released(workspace: Workspace, ports: tuple[int, ...]) -> None:
    require(not workspace.runtime_owned, "Runtime lock remains owned")
    require(
        workspace.previews is not None and not workspace.previews.has_owner, "Preview remains owned"
    )
    assert workspace.previews is not None
    require(workspace.previews.registry.read() is None, "Registry remains occupied")
    for port in ports:
        async with asyncio.timeout(2):
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
            except OSError:
                continue
            writer.close()
            await writer.wait_closed()
            raise RuntimeError("Application listener remains open")
    # Verify lock release by acquiring with a new facade; no reset/source Git is necessary.
    replacement = Workspace(workspace.runtime_root, REPO_ROOT / "demo-repo", pytest_pin="9.1.1")
    await replacement.acquire_runtime()
    replacement.release_runtime()


async def story(running: AcceptanceServer) -> tuple[int, int]:
    async with (
        httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as shop,
        Events(running.client) as events,
    ):
        return await Scenario(running.client, factory(running.client), shop, running.origin).run(
            events
        )


async def candidate(running: AcceptanceServer) -> Candidate:
    """Prepare revision two for browser proof, without ever approving or changing main."""
    async with (
        httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as shop,
        Events(running.client) as events,
    ):
        scenario = Scenario(running.client, factory(running.client), shop, running.origin)
        await scenario.preflight(events)
        from oversight.agents import parse_script

        created = await scenario.request(
            "POST",
            "/agents",
            json={
                "slot": "A",
                "model": "fake",
                "task": parse_script(SCRIPT.read_text()).task,
            },
        )
        scenario.agent_id, scenario.change_id = created["agent_id"], created["change_id"]
        first = await scenario.proposal(events, 1, 0)
        await events.wait(
            "agent.status",
            agent_id=first.agent_id,
            state="ready_for_review",
            after=events.seen[-1].seq,
        )
        await scenario.request(
            "POST",
            f"/changes/{first.change_id}/request-changes",
            json={
                **first.decision(),
                "comment": "Now add DEMO10 at ten percent and update its test.",
            },
        )
        second = await scenario.proposal(events, 2, events.seen[-1].seq)
        await events.wait(
            "agent.status",
            agent_id=second.agent_id,
            state="ready_for_review",
            after=events.seen[-1].seq,
        )
        return second


def namespace_members(namespace: str) -> list[int]:
    members: list[int] = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdecimal():
            with contextlib.suppress(FileNotFoundError, ProcessLookupError, PermissionError):
                if os.readlink(entry / "ns/pid") == namespace:
                    members.append(int(entry.name))
    return members
