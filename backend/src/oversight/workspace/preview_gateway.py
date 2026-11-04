"""Supervisor-owned HTTP leaf. Construction does not wire or authorize execution.

The connector alone chooses the private destination. The caller supplies already assigned
loopback authorities, a pinned publication and fresh-run liveness; no value comes from T.
Successful construction transfers listener/build ownership, never connector ownership.
"""

import asyncio
import contextlib
import json
import mimetypes
import re
import secrets
import socket
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import unquote_to_bytes, urlsplit


class ApiConnector(Protocol):
    async def connect_api(self) -> socket.socket:
        """Transfer one connected private-API socket; cancellation must close partial setup."""
        ...


class AssetSource(Protocol):
    @property
    def paths(self) -> tuple[str, ...]: ...

    def size(self, path: str) -> int: ...
    def read(self, path: str, offset: int = 0, size: int = 65536) -> bytes: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class GatewayLimits:
    header_bytes: int = 16_384
    body_bytes: int = 1_048_576
    response_bytes: int = 16_777_216
    sse_bytes: int = 268_435_456
    connections: int = 32
    request_s: float = 5
    io_s: float = 20
    response_s: float = 30
    sse_s: float = 900
    evidence_s: float = 5

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (
                self.header_bytes,
                self.body_bytes,
                self.response_bytes,
                self.sse_bytes,
                self.connections,
            )
        ):
            raise ValueError("invalid gateway byte/count limits")
        if any(
            not 0 < value <= 3600
            for value in (self.request_s, self.io_s, self.response_s, self.sse_s, self.evidence_s)
        ):
            raise ValueError("invalid gateway deadlines")


DEFAULT_GATEWAY_LIMITS = GatewayLimits()


def _sse_run_ids(data: bytes) -> tuple[str, ...]:
    """Only run.started identifies a run; ignore other complete SSE events."""
    runs: list[str] = []
    for frame in data.replace(b"\r\n", b"\n").split(b"\n\n")[:-1]:
        fields = [line[5:].lstrip(b" ") for line in frame.split(b"\n") if line.startswith(b"data:")]
        if not fields:
            continue
        event = json.loads(b"\n".join(fields))
        if not isinstance(event, dict):
            raise ValueError("invalid nested SSE envelope")
        envelope = cast(dict[str, object], event)
        if envelope.get("type") != "run.started":
            continue
        payload = envelope.get("payload")
        run_id = (
            cast(dict[str, object], payload).get("run_id") if isinstance(payload, dict) else None
        )
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("missing nested SSE run identity")
        runs.append(run_id)
    return tuple(runs)


@dataclass(frozen=True)
class ReadyEvidence:
    run_id: str
    checked_at: float
    assets: tuple[str, ...]
    workers: tuple[str, ...]
    api_path: str
    sse_run_id: str


@dataclass(frozen=True)
class _Message:
    line: str
    headers: tuple[tuple[str, str], ...]

    def values(self, key: str) -> list[str]:
        return [value for name, value in self.headers if name == key]


class _Refused(ValueError):
    def __init__(self, status: int = 400):
        self.status = status


_API = frozenset(
    {
        "health",
        "graph",
        "impact",
        "history",
        "usage",
        "agents",
        "leads",
        "plans",
        "queue",
        "changes",
        "grants",
        "merge-mode",
        "files",
        "tree",
    }
)
_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def _authority(value: str) -> None:
    if not re.fullmatch(r"(?:localhost|127\.0\.0\.1):[1-9][0-9]{0,4}", value):
        raise ValueError("exact loopback authority required")
    if int(value.rsplit(":", 1)[1]) > 65535:
        raise ValueError("invalid authority port")


def _path(target: str) -> str:
    raw = target.split("?", 1)[0]
    if not raw.startswith("/") or raw.startswith("//") or "#" in target:
        raise _Refused()
    if re.search(r"%(?![a-fA-F0-9]{2})|%(?:2f|5c)", raw, re.I):
        raise _Refused()
    path = unquote_to_bytes(raw).decode("utf-8", errors="strict")[1:]
    if not path:
        return "index.html"
    for part in path.split("/"):
        stem = part.split(".", 1)[0].upper()
        if (
            not part
            or part in {".", ".."}
            or part.endswith((".", " "))
            or any(ord(c) < 32 or ord(c) == 127 or c in '\\/:<>"|?*%' for c in part)
            or stem in {"CON", "NUL", "AUX", "PRN", "CONIN$", "CONOUT$"}
            or re.fullmatch(r"(?:COM|LPT)[0-9¹²³]", stem)
        ):
            raise _Refused()
    return path


async def _head(reader: asyncio.StreamReader, limit: int) -> _Message:
    raw = await reader.readuntil(b"\r\n\r\n")
    if len(raw) > limit:
        raise _Refused(431)
    lines = raw[:-4].decode("ascii").split("\r\n")
    fields: list[tuple[str, str]] = []
    for line in lines[1:]:
        key, separator, value = line.partition(":")
        if (
            not separator
            or not _TOKEN.fullmatch(key)
            or any((ord(c) < 32 and c != "\t") or ord(c) == 127 for c in value)
        ):
            raise _Refused()
        fields.append((key.lower(), value.strip(" \t")))
    return _Message(lines[0], tuple(fields))


def _length(message: _Message, maximum: int, *, upstream: bool = False) -> int | None:
    lengths, transfer = message.values("content-length"), message.values("transfer-encoding")
    if transfer:
        if upstream and not lengths and transfer == ["chunked"]:
            return None
        raise _Refused()
    if len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]{1,10}", lengths[0])):
        raise _Refused()
    size = int(lengths[0]) if lengths else 0
    if size > maximum:
        raise _Refused(413)
    return size


async def _body(
    reader: asyncio.StreamReader, message: _Message, maximum: int, idle_s: float
) -> AsyncIterator[bytes]:
    length = _length(message, maximum, upstream=True)
    remaining = length if message.values("content-length") else None
    chunked = bool(message.values("transfer-encoding"))
    total = 0
    while remaining is None or remaining:
        async with asyncio.timeout(idle_s):
            if chunked:
                line = await reader.readuntil(b"\r\n")
                if not re.fullmatch(rb"[0-9a-fA-F]{1,8}\r\n", line):
                    raise _Refused(502)
                count = int(line, 16)
                if count == 0:
                    if await reader.readexactly(2) != b"\r\n":
                        raise _Refused(502)
                    return
                if count > maximum - total:
                    raise _Refused(502)
                # No chunk-sized allocation: upstream chunk declarations may be large.
                left = count
                while left:
                    data = await reader.readexactly(min(left, 65536))
                    left -= len(data)
                    total += len(data)
                    yield data
                if await reader.readexactly(2) != b"\r\n":
                    raise _Refused(502)
                continue
            data = await reader.read(min(65536, remaining) if remaining is not None else 65536)
        if not data:
            if remaining:
                raise _Refused(502)
            return
        total += len(data)
        if total > maximum:
            raise _Refused(502)
        if remaining is not None:
            remaining -= len(data)
        yield data


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(OSError, TimeoutError):
        async with asyncio.timeout(1):
            await writer.wait_closed()


class PreviewGateway:
    def __init__(
        self,
        listener: socket.socket,
        build: AssetSource,
        connector: ApiConnector,
        *,
        public_authority: str,
        api_authority: str,
        parent_origins: tuple[str, ...],
        expected_run: str,
        backend_alive: Callable[[], bool],
        limits: GatewayLimits = DEFAULT_GATEWAY_LIMITS,
    ) -> None:
        _authority(public_authority)
        _authority(api_authority)
        if not parent_origins or not expected_run:
            raise ValueError("parent origins and fresh run required")
        for origin in parent_origins:
            if not origin.startswith("http://"):
                raise ValueError("exact parent origin required")
            _authority(origin[7:])
        address = listener.getsockname()
        if (
            listener.family != socket.AF_INET
            or address[0] != "127.0.0.1"
            or address[1] != int(public_authority.rsplit(":", 1)[1])
            or not listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
        ):
            raise ValueError("assigned loopback listener required")
        self.listener, self.build, self.connector = listener, build, connector
        self.public_authority, self.api_authority = public_authority, api_authority
        self.parent_origins, self.expected_run = parent_origins, expected_run
        self.backend_alive, self.limits = backend_alive, limits
        self._server: asyncio.Server | None = None
        self._watcher: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._evidence: ReadyEvidence | None = None
        self._nonce: str | None = None
        self._probing = False
        self._closed = False
        self._closing: asyncio.Task[None] | None = None
        self._responded: set[asyncio.StreamWriter] = set()
        self._stopped = asyncio.Event()
        self._policy = (
            "default-src 'self'; connect-src 'self'; worker-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; form-action 'self'; "
            "base-uri 'none'; frame-ancestors " + " ".join(parent_origins)
        )

    async def start(self) -> None:
        if self._closed or self._server is not None:
            raise ValueError("gateway cannot start")
        try:
            self._server = await asyncio.start_server(
                self._accept, sock=self.listener, limit=self.limits.header_bytes
            )
            if not self.alive():
                raise ValueError("private backend is unavailable")
            self._watcher = asyncio.create_task(self._watch())
        except BaseException:
            await self.close()
            raise

    def alive(self) -> bool:
        alive = not self._closed and self._server is not None and self._server.is_serving()
        if alive:
            try:
                alive = self.backend_alive()
            except Exception:
                alive = False
        if not alive:
            self._evidence, self._nonce = None, None
        return alive

    async def _watch(self) -> None:
        while self.alive():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopped.wait(), 0.05)
        await self.close()

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self._tasks) >= self.limits.connections or not self.alive():
            writer.close()
            return
        task = asyncio.create_task(self._handle(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(
        self,
        writer: asyncio.StreamWriter,
        code: int,
        body: bytes,
        content_type: str = "text/plain",
        *,
        head: bool = False,
    ) -> None:
        await self._headers(writer, code, content_type, len(body))
        if not head:
            writer.write(body)
            await asyncio.wait_for(writer.drain(), self.limits.io_s)

    async def _headers(
        self,
        writer: asyncio.StreamWriter,
        code: int,
        content_type: str,
        length: int | None = None,
        location: str | None = None,
    ) -> None:
        self._responded.add(writer)
        fields = [
            ("Content-Type", content_type),
            ("Content-Security-Policy", self._policy),
            ("Referrer-Policy", "no-referrer"),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Connection", "close"),
        ]
        if length is not None:
            fields.append(("Content-Length", str(length)))
        if location is not None:
            fields.append(("Location", location))
        writer.write(
            f"HTTP/1.1 {code} Response\r\n".encode()
            + b"".join(f"{key}: {value}\r\n".encode() for key, value in fields)
            + b"\r\n"
        )
        await asyncio.wait_for(writer.drain(), self.limits.io_s)

    def _admit(self, request: _Message) -> tuple[str, str, str]:
        parts = request.line.split(" ")
        if (
            len(parts) != 3
            or parts[2] != "HTTP/1.1"
            or parts[0] not in {"GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"}
        ):
            raise _Refused()
        method, target, _ = parts
        if any(ord(c) < 33 or ord(c) > 126 for c in target):
            raise _Refused()
        path = _path(target)
        if request.values("host") != [self.public_authority]:
            raise _Refused(403)
        origins = request.values("origin")
        if origins and origins != ["http://" + self.public_authority]:
            raise _Refused(403)
        if request.values("upgrade") or any(
            "upgrade" in v.lower() for v in request.values("connection")
        ):
            raise _Refused(426)
        if request.values("expect"):
            raise _Refused(417)
        return method, target, path

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(self.limits.request_s):
                request = await _head(reader, self.limits.header_bytes)
                method, target, path = self._admit(request)
                size = _length(request, self.limits.body_bytes)
                body = await reader.readexactly(size or 0)
            async with asyncio.timeout(
                self.limits.sse_s if path == "events" else self.limits.response_s
            ):
                serving = asyncio.create_task(
                    self._dispatch(writer, request, method, target, path, body)
                )
                peer = asyncio.create_task(reader.read(1))
                try:
                    done, _ = await asyncio.wait(
                        {serving, peer}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if serving in done:
                        await serving
                finally:
                    serving.cancel()
                    peer.cancel()
                    await asyncio.gather(serving, peer, return_exceptions=True)
        except (
            OSError,
            TimeoutError,
            ValueError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ) as error:
            with contextlib.suppress(OSError, TimeoutError):
                if writer in self._responded:
                    return
                await self._send(
                    writer,
                    error.status if isinstance(error, _Refused) else 400,
                    b"Preview request refused.\n",
                )
        finally:
            self._responded.discard(writer)
            await _close(writer)

    async def _dispatch(
        self,
        writer: asyncio.StreamWriter,
        request: _Message,
        method: str,
        target: str,
        path: str,
        body: bytes,
    ) -> None:
        if path == "_oversight_ready":
            if method != "GET" or body or not self.alive() or self._nonce is None:
                raise _Refused(403)
            await self._send(
                writer, 200, json.dumps({"nonce": self._nonce}).encode(), "application/json"
            )
        elif path == "events" or path.split("/", 1)[0] in _API:
            if path == "events" and method != "GET":
                raise _Refused(405)
            await self._proxy(writer, request, method, target, path, body)
        elif method in {"GET", "HEAD"} and not body and path in self.build.paths:
            await self._asset(writer, path, method == "HEAD")
        else:
            raise _Refused(404)

    async def _asset(self, writer: asyncio.StreamWriter, path: str, head: bool) -> None:
        size = self.build.size(path)
        await self._headers(
            writer, 200, mimetypes.guess_type(path)[0] or "application/octet-stream", size
        )
        if not head:
            offset = 0
            while offset < size:
                data = await asyncio.to_thread(
                    self.build.read, path, offset, min(65536, size - offset)
                )
                if not data:
                    raise _Refused(502)
                writer.write(data)
                await asyncio.wait_for(writer.drain(), self.limits.io_s)
                offset += len(data)

    def _redirect(self, values: list[str]) -> str | None:
        if not values:
            return None
        if len(values) != 1 or "\\" in values[0] or any(ord(c) < 33 for c in values[0]):
            raise _Refused(502)
        value = values[0]
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc:
            if parsed.scheme != "http" or parsed.netloc != self.public_authority:
                raise _Refused(502)
        elif not value.startswith("/") or value.startswith("//"):
            raise _Refused(502)
        try:
            _path(parsed.path or "/")
        except ValueError:
            raise _Refused(502) from None
        return value

    async def _upstream(
        self, request: _Message, method: str, target: str, body: bytes
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        sock = await asyncio.wait_for(self.connector.connect_api(), self.limits.io_s)
        try:
            sock.setblocking(False)
            reader, writer = await asyncio.open_connection(
                sock=sock, limit=self.limits.header_bytes
            )
        except BaseException:
            sock.close()
            raise
        try:
            # Allowlist strips Forwarded, X-Forwarded-*, proxy/hop headers and all
            # Connection-nominated fields. Host is always supervisor-selected.
            nominated = {
                v.strip().lower()
                for value in request.values("connection")
                for v in value.split(",")
            }
            allowed = {"accept", "content-type", "last-event-id", "origin"} - nominated
            fields = [(key, value) for key, value in request.headers if key in allowed]
            fields += [
                ("host", self.api_authority),
                ("connection", "close"),
                ("content-length", str(len(body))),
            ]
            writer.write(
                f"{method} {target} HTTP/1.1\r\n".encode()
                + b"".join(f"{k}: {v}\r\n".encode() for k, v in fields)
                + b"\r\n"
                + body
            )
            await asyncio.wait_for(writer.drain(), self.limits.io_s)
            return reader, writer
        except BaseException:
            await _close(writer)
            raise

    async def _proxy(
        self,
        client: asyncio.StreamWriter,
        request: _Message,
        method: str,
        target: str,
        path: str,
        body: bytes,
    ) -> None:
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await self._upstream(request, method, target, body)
            response = await asyncio.wait_for(
                _head(reader, self.limits.header_bytes), self.limits.io_s
            )
            if not re.fullmatch(r"HTTP/1\.[01] [2-5][0-9]{2}(?: [\x20-\x7e]*)?", response.line):
                raise _Refused(502)
            code = int(response.line.split(" ")[1])
            maximum = self.limits.sse_bytes if path == "events" else self.limits.response_bytes
            _length(response, maximum, upstream=True)
            types = response.values("content-type")
            if len(types) > 1:
                raise _Refused(502)
            content_type = types[0] if types else "application/octet-stream"
            location = self._redirect(response.values("location"))
            if path == "events":
                if code != 200 or content_type.split(";", 1)[0] != "text/event-stream":
                    raise _Refused(502)
                await self._headers(client, code, content_type)
                async for chunk in _body(reader, response, maximum, self.limits.io_s):
                    client.write(chunk)
                    await asyncio.wait_for(client.drain(), self.limits.io_s)
                self._evidence, self._nonce = None, None
            else:
                data = bytearray()
                if method != "HEAD" and code not in {204, 304}:
                    async for chunk in _body(reader, response, maximum, self.limits.io_s):
                        data.extend(chunk)
                await self._headers(client, code, content_type, len(data), location)
                client.write(data)
                await asyncio.wait_for(client.drain(), self.limits.io_s)
        except (
            OSError,
            TimeoutError,
            ValueError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            self._evidence, self._nonce = None, None
            raise
        finally:
            if writer is not None:
                await _close(writer)

    async def _probe_get(self, path: str, *, sse: bool = False) -> bytes:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", self.listener.getsockname()[1], limit=self.limits.header_bytes
        )
        try:
            writer.write(f"GET {path} HTTP/1.1\r\nHost: {self.public_authority}\r\n\r\n".encode())
            await writer.drain()
            response = await _head(reader, self.limits.header_bytes)
            if response.line.split(" ")[1] != "200":
                raise ValueError("readiness resource unavailable")
            data = bytearray()
            async for chunk in _body(
                reader, response, self.limits.response_bytes, self.limits.io_s
            ):
                data.extend(chunk)
                if sse and _sse_run_ids(bytes(data)):
                    return bytes(data)
            return bytes(data)
        finally:
            await _close(writer)

    async def probe(
        self,
        *,
        assets: tuple[str, ...],
        workers: tuple[str, ...],
        api_path: str = "/graph",
        within_s: float = 30,
    ) -> ReadyEvidence:
        if self._probing or not 0 < within_s <= 3600:
            raise ValueError("invalid or overlapping readiness probe")
        assets, workers = tuple(assets), tuple(workers)
        self._probing = True
        self._evidence, self._nonce = None, None
        try:
            async with asyncio.timeout(min(within_s, self.limits.response_s)):
                if (
                    not self.alive()
                    or not assets
                    or not workers
                    or _path(api_path).split("/")[0] not in _API
                ):
                    raise ValueError("complete readiness resources required")
                health = json.loads(await self._probe_get("/health"))
                if health.get("run_id") != self.expected_run or health.get("ok") is not True:
                    raise ValueError("stale nested health")
                if not await self._probe_get("/"):
                    raise ValueError("empty nested index")
                for path in (*assets, *workers):
                    if _path(path) not in self.build.paths or not await self._probe_get(path):
                        raise ValueError("missing nested resource")
                api = json.loads(await self._probe_get(api_path))
                if api.get("run_id") != self.expected_run:
                    raise ValueError("stale nested API")
                raw = await self._probe_get("/events", sse=True)
                runs = _sse_run_ids(raw)
                if not runs or any(run_id != self.expected_run for run_id in runs):
                    raise ValueError("stale nested SSE")
                if not self.alive():
                    raise ValueError("service died during readiness")
                evidence = ReadyEvidence(
                    self.expected_run,
                    time.monotonic(),
                    assets,
                    workers,
                    api_path,
                    self.expected_run,
                )
                self._evidence = evidence
                return evidence
        except BaseException:
            self._evidence, self._nonce = None, None
            raise

        finally:
            self._probing = False

    def authorize_ready(self, evidence: ReadyEvidence | None, *, expected_run: str) -> str:
        if (
            not self.alive()
            or self._probing
            or evidence is None
            or evidence is not self._evidence
            or expected_run != self.expected_run
            or evidence.run_id != expected_run
            or time.monotonic() - evidence.checked_at > self.limits.evidence_s
        ):
            self._evidence, self._nonce = None, None
            raise ValueError("fresh supervisor-bound readiness evidence required")
        self._nonce = secrets.token_hex(32)
        return self._nonce

    async def close(self) -> None:
        if self._closing is None:
            self._closing = asyncio.create_task(self._shutdown())
        cancelled = False
        current = asyncio.current_task()
        for task in (self._closing, self._watcher):
            if task is None or task is current:
                continue
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
            task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _shutdown(self) -> None:
        self._closed = True
        self._stopped.set()
        self._evidence, self._nonce = None, None
        if self._server is not None:
            self._server.close()
        self.listener.close()
        current = asyncio.current_task()
        tasks = [task for task in self._tasks if task is not current]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()
        await asyncio.to_thread(self.build.close)
