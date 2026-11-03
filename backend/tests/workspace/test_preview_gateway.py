"""Socket fixtures exercise the isolated HTTP leaf, not deployed readiness."""

import asyncio
import json
import socket
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError, replace

import pytest

from oversight.workspace.preview_gateway import GatewayLimits, PreviewGateway, ReadyEvidence
from oversight.workspace.preview_publication import publish_build

pytestmark = pytest.mark.anyio
RUN = "fresh-run"
PARENT = "http://localhost:8710"


def envelope(kind: str, payload: object, *, seq: int = 1) -> dict[str, object]:
    return {"seq": seq, "ts": "2026-11-03T12:00:00Z", "type": kind, "payload": payload}


def sse_frame(event: dict[str, object]) -> bytes:
    return f"id: {event.get('seq', 1)}\ndata: ".encode() + json.dumps(event).encode() + b"\n\n"


class Backend:
    def __init__(self):
        self.alive = True
        self.health_run = self.api_run = self.sse_run = RUN
        self.headers = []
        self.extra = b""
        self.raw_response: bytes | None = None
        self.tasks = set()
        self.sse_open = False
        self.sse_closed = asyncio.Event()
        self.sse_events: list[dict[str, object]] | None = None
        self.sse_complete = False
        self.fail_path = ""

    def accept(self, reader, writer):
        task = asyncio.create_task(self.handle(reader, writer))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def handle(self, reader, writer):
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
            self.headers.append(raw.lower())
            path = raw.split(b" ")[1].decode().split("?", 1)[0]
            fields = raw.decode().split("\r\n")
            length = next(
                (
                    int(v.split(":", 1)[1])
                    for v in fields
                    if v.lower().startswith("content-length:")
                ),
                0,
            )
            await reader.readexactly(length)
            if self.raw_response is not None:
                writer.write(self.raw_response)
            elif path == "/events":
                self.sse_open = True
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                    b"Transfer-Encoding: chunked\r\n" + self.extra + b"\r\n"
                )
                events = self.sse_events
                if events is None:
                    events = [envelope("run.started", {"run_id": self.sse_run})]
                for event in events:
                    frame = sse_frame(event)
                    writer.write(f"{len(frame):x}\r\n".encode() + frame + b"\r\n")
                    await writer.drain()
                    await asyncio.sleep(0.01)
                if self.sse_complete:
                    writer.write(b"0\r\n\r\n")
                else:
                    await reader.read()
            else:
                run = self.health_run if path == "/health" else self.api_run
                body = json.dumps({"ok": True, "run_id": run}).encode()
                status = b"404 Missing" if path == self.fail_path else b"200 OK"
                writer.write(
                    b"HTTP/1.1 "
                    + status
                    + b"\r\nContent-Type: application/json\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n"
                    + self.extra
                    + b"\r\n"
                    + body
                )
            await writer.drain()
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            if self.sse_open:
                self.sse_closed.set()


class Connector:
    def __init__(self, port):
        self.port = port
        self.calls = 0
        self.sockets = []

    async def connect_api(self):
        self.calls += 1
        sock = socket.socket()
        self.sockets.append(sock)
        sock.setblocking(False)
        try:
            await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", self.port))
            return sock
        except BaseException:
            sock.close()
            raise


@asynccontextmanager
async def running(tmp_path, *, limits=None, public_host="127.0.0.1"):
    source = tmp_path / "tree"
    source.mkdir()
    (source / "assets").mkdir()
    for name, data in [
        ("index.html", b'<script src="/assets/app.js"></script>'),
        ("assets/app.js", b"app"),
        ("assets/worker.js", b"worker"),
    ]:
        (source / name).write_bytes(data)
    build = await publish_build(source, tmp_path / "published", tested_tree=source)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    authority = f"{public_host}:{port}"
    backend = Backend()
    server = await asyncio.start_server(backend.accept, "127.0.0.1", 0)
    connector = Connector(server.sockets[0].getsockname()[1])
    gateway = PreviewGateway(
        listener,
        build,
        connector,
        public_authority=authority,
        api_authority=f"127.0.0.1:{connector.port}",
        parent_origins=(PARENT,),
        expected_run=RUN,
        backend_alive=lambda: backend.alive,
        limits=limits or GatewayLimits(request_s=0.15, io_s=0.5, response_s=2),
    )
    try:
        await gateway.start()
        yield gateway, backend, connector, port, authority
    finally:
        await gateway.close()
        server.close()
        await server.wait_closed()
        for task in backend.tasks:
            task.cancel()
        await asyncio.gather(*backend.tasks, return_exceptions=True)
        assert all(sock.fileno() == -1 for sock in connector.sockets)
        assert not (tmp_path / "published").exists()


async def request(port, authority, path="/", *, headers=None, method="GET", tail=b"", raw=None):
    async with asyncio.timeout(3):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            fields = [("Host", authority)] if headers is None else headers
            writer.write(
                raw
                if raw is not None
                else f"{method} {path} HTTP/1.1\r\n".encode()
                + b"".join(f"{k}: {v}\r\n".encode() for k, v in fields)
                + b"\r\n"
                + tail
            )
            await writer.drain()
            data = await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
    head, body = data.split(b"\r\n\r\n", 1)
    lines = head.decode().split("\r\n")
    return int(lines[0].split(" ")[1]), dict(line.split(": ", 1) for line in lines[1:]), body


@pytest.mark.parametrize("public_host", ["127.0.0.1", "localhost"])
async def test_gateway_authority_and_fixed_routing(tmp_path, public_host):
    async with running(tmp_path, public_host=public_host) as (
        _,
        backend,
        connector,
        port,
        authority,
    ):
        assert (await request(port, authority))[0] == 200
        assert connector.calls == 0
        for fields in [
            [],
            [("Host", "foreign:80")],
            [("Host", authority)] * 2,
            [("Host", authority), ("Origin", PARENT)],
            [("Host", authority), ("Origin", "null")],
            [("Host", authority), ("Origin", "http://" + authority)] * 2,
        ]:
            assert (await request(port, authority, headers=fields))[0] == 403
        for path in [
            "/assets/",
            "/missing",
            "/previews",
            "//evil/health",
            "/%2e%2e/health",
            "/a%2fhealth",
        ]:
            assert (await request(port, authority, path))[0] in {400, 404}
        assert connector.calls == 0
        assert (
            await request(
                port,
                authority,
                "/graph",
                headers=[("Host", authority), ("Origin", "http://" + authority)],
            )
        )[0] == 200
        assert connector.calls == 1 and b"get /graph " in backend.headers[-1]


@pytest.mark.parametrize(
    "attack",
    [
        b"Content-Length: 1\r\nContent-Length: 1\r\n",
        b"Transfer-Encoding: chunked\r\n",
        b"Content-Length: -1\r\n",
        b"Content-Length: 999999999\r\n",
        b" Folded: x\r\n",
        b"Bad Header: x\r\n",
        b"X: a\nb\r\n",
        b"Expect: 100-continue\r\n",
        b"Upgrade: websocket\r\n",
        b"Connection: upgrade\r\n",
    ],
)
async def test_gateway_bounds_headers_redirects_and_upgrades_framing(tmp_path, attack):
    async with running(tmp_path) as (_, _, connector, port, authority):
        raw = b"GET /graph HTTP/1.1\r\nHost: " + authority.encode() + b"\r\n" + attack + b"\r\n"
        assert (await request(port, authority, raw=raw))[0] >= 400
        assert connector.calls == 0


async def test_gateway_bounds_headers_redirects_and_upgrades(tmp_path):
    async with running(tmp_path) as (_, backend, _, port, authority):
        fields = [
            ("Host", authority),
            ("Forwarded", "host=evil"),
            ("X-Forwarded-Host", "evil"),
            ("X-Forwarded-For", "evil"),
            ("Proxy-Authorization", "secret"),
            ("Connection", "content-type"),
            ("Content-Type", "evil"),
        ]
        assert (await request(port, authority, "/graph", headers=fields))[0] == 200
        assert b"forwarded" not in backend.headers[-1] and b"evil" not in backend.headers[-1]
        assert b"proxy-authorization" not in backend.headers[-1]
        for location in [
            "https://evil/",
            "//evil/x",
            "/%2f/evil",
            "/\\evil",
            "http://user@" + authority,
        ]:
            backend.extra = f"Location: {location}\r\n".encode()
            assert (await request(port, authority, "/graph"))[0] == 502
        backend.extra = b"Location: /graph\r\n"
        assert (await request(port, authority, "/graph"))[1]["Location"] == "/graph"
        backend.extra = f"Location: http://{authority}/graph\r\n".encode()
        assert (await request(port, authority, "/graph"))[0] == 200
        backend.raw_response = b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nx"
        assert (await request(port, authority, "/graph"))[0] == 400
        assert (await request(port, authority, raw=b"GET / HTTP/1.1\r\n"))[0] == 400
        assert (await request(port, authority, headers=[("Host", authority), ("X", "x" * 17000)]))[
            0
        ] >= 400


async def test_gateway_response_policy(tmp_path):
    async with running(tmp_path) as (_, backend, _, port, authority):
        backend.extra = (
            b"Content-Security-Policy: default-src *; sandbox allow-scripts\r\n"
            b"Set-Cookie: unsafe=x\r\nAccess-Control-Allow-Origin: *\r\n"
            b"Referrer-Policy: unsafe-url\r\nCache-Control: public\r\n"
        )
        expected = (
            "default-src 'self'; connect-src 'self'; worker-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; form-action 'self'; "
            "base-uri 'none'; frame-ancestors " + PARENT
        )
        for path in ["/", "/graph", "/missing", "/_oversight_ready"]:
            _, headers, _ = await request(port, authority, path)
            assert headers["Content-Security-Policy"] == expected
            assert headers["Referrer-Policy"] == "no-referrer"
            assert headers["Cache-Control"] == "no-store"
            assert "Set-Cookie" not in headers and "Access-Control-Allow-Origin" not in headers


async def probe(gateway):
    return await gateway.probe(assets=("/assets/app.js",), workers=("/assets/worker.js",))


async def test_gateway_fresh_readiness_and_nonce_authorization(tmp_path):
    async with running(tmp_path) as (gateway, backend, _, port, authority):
        assert (await request(port, authority, "/_oversight_ready"))[0] == 403
        with pytest.raises(ValueError):
            gateway.authorize_ready(None, expected_run=RUN)
        forged = ReadyEvidence(RUN, 0, ("/assets/app.js",), ("/assets/worker.js",), "/graph", RUN)
        with pytest.raises(ValueError):
            gateway.authorize_ready(forged, expected_run=RUN)
        evidence = await probe(gateway)
        with pytest.raises(FrozenInstanceError):
            evidence.run_id = "other"
        assert (await request(port, authority, "/_oversight_ready"))[0] == 403
        with pytest.raises(ValueError):
            gateway.authorize_ready(evidence, expected_run="other")
        evidence = await probe(gateway)
        nonce = gateway.authorize_ready(evidence, expected_run=RUN)
        assert len(nonce) == 64
        assert json.loads((await request(port, authority, "/_oversight_ready"))[2]) == {
            "nonce": nonce
        }
        backend.alive = False
        assert not gateway.alive()
        with pytest.raises(ValueError):
            gateway.authorize_ready(evidence, expected_run=RUN)
        await asyncio.sleep(0.1)
        assert gateway.listener.fileno() == -1


async def test_gateway_readiness_ignores_events_without_run_identity(tmp_path):
    async with running(tmp_path) as (gateway, backend, _, _, _):
        backend.sse_events = [
            envelope("graph.snapshot", {"nodes": [], "edges": []}),
            envelope("run.started", {"run_id": RUN}, seq=2),
        ]
        evidence = await probe(gateway)
        assert evidence.sse_run_id == RUN
        assert gateway.authorize_ready(evidence, expected_run=RUN)


@pytest.mark.parametrize(
    "event",
    [
        envelope("graph.snapshot", {"run_id": RUN}),
        {"run_id": RUN},
        envelope("run.started", {"run_id": "old"}),
        envelope("run.started", {}),
        envelope("run.started", None),
    ],
)
async def test_gateway_readiness_requires_run_started_identity(tmp_path, event):
    async with running(tmp_path) as (gateway, backend, _, port, authority):
        backend.sse_events = [event]
        backend.sse_complete = True
        with pytest.raises(ValueError, match="SSE"):
            await probe(gateway)
        assert gateway._evidence is None
        assert (await request(port, authority, "/_oversight_ready"))[0] == 403


@pytest.mark.parametrize(
    "failure",
    [
        "health",
        "sse",
        "api",
        "api_missing",
        "index",
        "asset",
        "worker",
        "partial",
        "death",
        "gateway",
        "stale",
        "forged",
    ],
)
async def test_gateway_readiness_refuses_partial_stale_and_mismatched(tmp_path, failure):
    async with running(tmp_path) as (gateway, backend, _, _, _):
        if failure in {"health", "sse", "api"}:
            setattr(backend, failure + "_run", "old")
        elif failure == "api_missing":
            backend.fail_path = "/graph"
        elif failure == "gateway":
            assert gateway._server is not None
            gateway._server.close()
        elif failure in {"index", "asset", "worker"}:
            missing = {
                "index": "index.html",
                "asset": "assets/app.js",
                "worker": "assets/worker.js",
            }[failure]
            object.__setattr__(
                gateway.build,
                "paths",
                tuple(path for path in gateway.build.paths if path != missing),
            )
        elif failure == "partial":
            with pytest.raises(ValueError):
                await gateway.probe(assets=(), workers=())
            return
        elif failure == "death":
            backend.alive = False
        elif failure in {"stale", "forged"}:
            evidence = await probe(gateway)
            if failure == "stale":
                gateway.limits = replace(gateway.limits, evidence_s=0.001)
                await asyncio.sleep(0.01)
            else:
                evidence = replace(evidence)
            with pytest.raises(ValueError):
                gateway.authorize_ready(evidence, expected_run=RUN)
            return
        with pytest.raises((ValueError, OSError)):
            await probe(gateway)


async def test_gateway_cancellation_peer_death_and_partial_setup(tmp_path):
    async with running(tmp_path) as (gateway, backend, _, port, authority):
        task = asyncio.create_task(probe(gateway))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"GET /events HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode())
        await writer.drain()
        assert b"200" in await reader.readuntil(b"\r\n\r\n")
        writer.close()
        await writer.wait_closed()
        async with asyncio.timeout(2):
            await backend.sse_closed.wait()
        await gateway.close()
        assert not gateway.alive() and not gateway._tasks


async def test_gateway_close_drains_active_sse(tmp_path):
    limits = GatewayLimits(io_s=5, sse_s=5)
    async with running(tmp_path, limits=limits) as (gateway, backend, connector, port, authority):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        closing = None
        try:
            writer.write(f"GET /events HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode())
            await writer.drain()
            async with asyncio.timeout(1):
                assert b"200" in await reader.readuntil(b"\r\n\r\n")
                assert await reader.readuntil(b"\n\n")
            assert gateway._tasks and not backend.sse_closed.is_set()
            closing = asyncio.create_task(gateway.close())
            done, _ = await asyncio.wait({closing}, timeout=0.5)
            assert closing in done, "close waited for the live SSE handler's deadline"
            await closing
            async with asyncio.timeout(1):
                await backend.sse_closed.wait()
                assert await reader.read() == b""
            assert not gateway._tasks
            assert gateway.listener.fileno() == -1
            assert all(sock.fileno() == -1 for sock in connector.sockets)
            assert not (tmp_path / "published").exists()
        finally:
            writer.close()
            await writer.wait_closed()
            if closing is not None:
                await closing


@pytest.mark.parametrize(
    "framing", ["length", "chunked", "truncated", "slow", "bad_chunk", "upgrade"]
)
async def test_gateway_upstream_response_bounds(tmp_path, framing):
    limits = GatewayLimits(response_bytes=64, io_s=0.1, response_s=0.3, request_s=0.1)
    async with running(tmp_path, limits=limits) as (_, backend, _, port, authority):
        if framing == "length":
            backend.raw_response = b"HTTP/1.1 200 OK\r\nContent-Length: 65\r\n\r\n"
        elif framing == "chunked":
            backend.raw_response = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n41\r\n"
        elif framing == "truncated":
            backend.raw_response = b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nx"
        elif framing == "slow":
            backend.raw_response = b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\n"
        elif framing == "bad_chunk":
            backend.raw_response = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nZ\r\n"
        else:
            backend.raw_response = b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n"
        assert (await request(port, authority, "/graph"))[0] >= 400


async def test_gateway_request_body_boundary_and_service_death_during_probe(tmp_path):
    limits = GatewayLimits(body_bytes=4, io_s=0.3, response_s=1)
    async with running(tmp_path, limits=limits) as (gateway, backend, _, port, authority):
        headers = [("Host", authority), ("Content-Length", "4")]
        assert (
            await request(port, authority, "/agents", method="POST", headers=headers, tail=b"1234")
        )[0] == 200
        headers[-1] = ("Content-Length", "5")
        assert (
            await request(port, authority, "/agents", method="POST", headers=headers, tail=b"12345")
        )[0] == 413
        original = gateway._probe_get

        async def die_on_api(path, *, sse=False):
            response = await original(path, sse=sse)
            if path == "/graph":
                backend.alive = False
            return response

        gateway._probe_get = die_on_api
        with pytest.raises((ValueError, OSError, asyncio.IncompleteReadError)):
            await probe(gateway)
        assert gateway._evidence is None


async def test_gateway_sse_streaming_and_limits(tmp_path):
    event = envelope("run.started", {"run_id": RUN})
    frame_bytes = len(sse_frame(event))
    limits = GatewayLimits(sse_bytes=frame_bytes, io_s=0.3, sse_s=0.4)
    async with running(tmp_path, limits=limits) as (_, backend, _, port, authority):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(f"GET /events HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode())
            await writer.drain()
            async with asyncio.timeout(0.3):
                assert b"200" in await reader.readuntil(b"\r\n\r\n")
                assert await reader.readuntil(b"\n\n") == sse_frame(event)
            # An open backend stream is terminated within the configured deadline.
            async with asyncio.timeout(1):
                assert await reader.read() == b""
        finally:
            writer.close()
            await writer.wait_closed()
        backend.raw_response = (
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: "
            + str(frame_bytes + 1).encode()
            + b"\r\n\r\n"
        )
        assert (await request(port, authority, "/events"))[0] == 413


async def test_gateway_partial_connector_setup_and_cancelled_close(tmp_path):
    async with running(tmp_path) as (gateway, _, connector, port, authority):
        entered = asyncio.Event()
        allocated = []

        async def incomplete():
            left, right = socket.socketpair()
            allocated.extend((left, right))
            entered.set()
            try:
                await asyncio.Event().wait()
                return left
            finally:
                left.close()
                right.close()

        connector.connect_api = incomplete
        task = asyncio.create_task(request(port, authority, "/graph"))
        try:
            async with asyncio.timeout(1):
                await entered.wait()
            closing = asyncio.create_task(gateway.close())
            await asyncio.sleep(0)
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert not gateway.alive()
            assert all(sock.fileno() == -1 for sock in allocated)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_gateway_failed_start_closes_transferred_resources(tmp_path):
    from unittest.mock import patch

    async with running(tmp_path) as (gateway, _, _, _, _):
        await gateway.close()
    source = tmp_path / "new-tree"
    source.mkdir()
    build = await publish_build(source, tmp_path / "published", tested_tree=source)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    gateway = PreviewGateway(
        listener,
        build,
        Connector(1),
        public_authority=f"127.0.0.1:{listener.getsockname()[1]}",
        api_authority="127.0.0.1:1",
        parent_origins=(PARENT,),
        expected_run=RUN,
        backend_alive=lambda: True,
    )
    with (
        patch("asyncio.start_server", side_effect=OSError("failed listener handoff")),
        pytest.raises(OSError, match="failed listener handoff"),
    ):
        await gateway.start()
    assert listener.fileno() == -1
    assert not (tmp_path / "published").exists()
