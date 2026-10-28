"""Only Oversight HTTP APIs; validated, resumable SSE with bounded reconnects."""

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import TypeAdapter
from tools.eval.tasks import BenchError

from oversight.telemetry import TRACE_HEADER
from oversight_contracts import CONTRACT_VERSION
from oversight_contracts.commands import Health
from oversight_contracts.events import ServerEvent

EVENT = TypeAdapter(ServerEvent)


class API:
    def __init__(self, server: str) -> None:
        self.trace_enabled = False
        self.trace_attempt: str | None = None
        parsed = urlparse(server)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise BenchError("server must be a local Oversight HTTP origin")
        self.client = httpx.AsyncClient(
            base_url=server.rstrip("/"),
            trust_env=False,
            timeout=httpx.Timeout(10, read=20),
            follow_redirects=False,
        )

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        if path == "/agents" and self.trace_attempt:
            kwargs["headers"] = {TRACE_HEADER: self.trace_attempt}
        # POST is deliberately never retried, including an ambiguous lost start response.
        deadline = 180 if path == "/agents" else 15
        async with asyncio.timeout(deadline):
            response = await self.client.request(method, path, timeout=deadline, **kwargs)
        if response.status_code not in {200, 201, 202}:
            raise BenchError(f"{method} {path}: HTTP {response.status_code}")
        return response.json() if response.content else None

    async def health(self, run_id: str | None = None) -> Health:
        health = Health.model_validate(await self.request("GET", "/health"))
        if not health.ok or health.mode != "live" or health.contract_version != CONTRACT_VERSION:
            raise BenchError("health is not a compatible live Oversight server")
        if health.missing_settings:
            raise BenchError("server reports incomplete provider setup")
        if run_id and health.run_id != run_id:
            raise BenchError("server restarted during task; reservation stays unsettled")
        return health


class Events:
    def __init__(self, api: API, run_id: str, evidence: Path) -> None:
        self.api, self.run_id, self.evidence = api, run_id, evidence
        self.seq = 0
        self.written: dict[tuple[str, int], str] = {}
        if evidence.exists():
            for line in evidence.read_text().splitlines():
                stored = json.loads(line)
                event = EVENT.validate_python(stored["event"])
                self.written[stored["run_id"], event.seq] = event.model_dump_json()
        self.seen: dict[tuple[str, int], str] = {}
        self.stream = self._connect()

    def accept(self, raw: str, event_id: str | None) -> Any | None:
        event = EVENT.validate_json(raw)
        if event_id != str(event.seq):
            raise BenchError("SSE id does not match event seq")
        key = self.run_id, event.seq
        canonical = event.model_dump_json()
        if key in self.seen:
            if self.seen[key] != canonical:
                raise BenchError("conflicting duplicate event")
            return None
        if event.seq != self.seq + 1:
            raise BenchError("SSE gap: cannot safely recover usage from a graph snapshot")
        if event.type == "run.started" and event.payload.run_id != self.run_id:
            raise BenchError("unexpected run.started")
        self.seen[key], self.seq = canonical, event.seq
        if key in self.written and self.written[key] != canonical:
            raise BenchError("event differs from durable evidence")
        if key not in self.written:
            with self.evidence.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"run_id": self.run_id, "event": json.loads(raw)}) + "\n")
                handle.flush()
            self.written[key] = canonical
        return event

    async def _connect(self) -> AsyncGenerator[Any, None]:
        failures = 0
        while failures < 4:
            await self.api.health(self.run_id)
            try:
                async with self.api.client.stream(
                    "GET",
                    "/events",
                    params={"run": self.run_id},
                    headers={"Last-Event-ID": str(self.seq)},
                ) as response:
                    if response.status_code != 200 or not response.headers.get(
                        "content-type", ""
                    ).startswith("text/event-stream"):
                        raise BenchError("invalid SSE response")
                    data: list[str] = []
                    event_id = None
                    async for line in response.aiter_lines():
                        if line == "":
                            if data:
                                event = self.accept("\n".join(data), event_id)
                                if event is not None:
                                    failures = 0
                                    yield event
                            data, event_id = [], None
                        elif line.startswith("data:"):
                            data.append(line[5:].removeprefix(" "))
                        elif line.startswith("id:"):
                            event_id = line[3:].strip()
            except (httpx.TransportError, OSError):
                pass
            failures += 1
            await asyncio.sleep(0.05 * failures)
        raise BenchError("SSE reconnect limit reached")

    async def next(self) -> Any:
        return await anext(self.stream)

    async def reconnect(self) -> None:
        await self.stream.aclose()
        self.stream = self._connect()

    async def close(self) -> None:
        await self.stream.aclose()
