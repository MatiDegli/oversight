"""Host/Origin admission is independent of routing and never consumes/buffers a body."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from starlette.types import Message

from oversight.api import ApiGuard, AuthorityPolicy
from oversight.app import ReplayConfig, create_app
from oversight.settings import REPO_ROOT, Settings, resolve_ports
from oversight_contracts.commands import ErrorResponse

pytestmark = pytest.mark.anyio
DEFAULT_POLICY = AuthorityPolicy(8710, 5710)


async def invoke(
    headers: list[tuple[bytes, bytes]],
    *,
    kind: str = "http",
    scheme: str = "http",
    method: str = "GET",
    path: str = "/",
    extension: bool = False,
    policy: AuthorityPolicy = DEFAULT_POLICY,
) -> tuple[list[Message], AsyncMock]:
    downstream = AsyncMock()
    receive = AsyncMock(side_effect=AssertionError("guard read a request body"))
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Any = {
        "type": kind,
        "scheme": scheme,
        "method": method,
        "path": path,
        "headers": headers,
        "extensions": {"websocket.http.response": {}} if extension else {},
    }
    await ApiGuard(downstream, policy=policy)(scope, receive, send)
    return sent, downstream


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@pytest.mark.parametrize(
    "path", ["/agents", "/files", "/events", "/tree", "/", "/assets/a.js", "/unknown"]
)
@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"host", b"attacker.test:8710")],
        [(b"host", b"localhost:8710"), (b"origin", b"https://attacker.test")],
    ],
)
async def test_every_method_and_path_refuses_before_routing(method, path, headers) -> None:
    sent, downstream = await invoke(headers, method=method, path=path)
    assert sent[0]["status"] == 403
    body = ErrorResponse.model_validate_json(sent[1]["body"])
    assert body.error.code == "forbidden"
    assert "Host" in body.error.message or "Origin" in body.error.message
    assert "OVERSIGHT_PORT" in body.error.message and "OVERSIGHT_VITE_PORT" in body.error.message
    assert "attacker" not in body.error.message
    assert not any(k.startswith(b"access-control") for k, _ in sent[0]["headers"])
    downstream.assert_not_called()


@pytest.mark.parametrize(
    "host",
    [
        b"localhost:8710",
        b"127.0.0.1:8710",
        b"[::1]:8710",
        b"localhost:5710",
        b"127.0.0.1:5710",
        b"[::1]:5710",
    ],
)
@pytest.mark.parametrize(
    "origin", [None, b"http://localhost:8710", b"http://127.0.0.1:5710", b"http://[::1]:5710"]
)
async def test_literal_loopback_authorities_and_cli_writes_pass(host, origin) -> None:
    headers = [(b"host", host)] + ([(b"origin", origin)] if origin else [])
    sent, downstream = await invoke(headers, method="PUT")
    assert not sent
    downstream.assert_awaited_once()


@pytest.mark.parametrize(
    "bad",
    [
        b"",
        b"localhost",
        b"localhost:0",
        b"localhost:65536",
        b"localhost:-1",
        b"localhost:+8710",
        b"localhost:8.710",
        b"localhost:8710/",
        b"localhost:8710 ",
        b" localhost:8710",
        b"localhost.:8710",
        b"localhost.attacker:8710",
        b"127.1:8710",
        b"2130706433:8710",
        b"[::ffff:127.0.0.1]:8710",
        b"::1:8710",
        b"user@localhost:8710",
        b"localhost:8710,localhost:5710",
        b"localhost:8710\xff",
    ],
)
async def test_malformed_or_foreign_host_and_forwarded_headers_are_refused(bad) -> None:
    sent, downstream = await invoke(
        [
            (b"host", bad),
            (b"x-forwarded-host", b"localhost:8710"),
            (b"forwarded", b"host=localhost:8710"),
        ]
    )
    assert sent[0]["status"] == 403
    downstream.assert_not_called()


@pytest.mark.parametrize(
    "bad",
    [
        b"",
        b"null",
        b"https://localhost:8710",
        b"http://localhost",
        b"http://localhost:0",
        b"http://localhost:65536",
        b"http://localhost:8711",
        b"http://localhost:8710/",
        b"http://localhost:8710/path",
        b"http://user@localhost:8710",
        b"http://localhost:8710?x",
        b"http://localhost:8710#x",
        b"http://localhost:8710 http://localhost:5710",
        b"http://localhost:8710,http://localhost:5710",
        b" http://localhost:8710",
    ],
)
async def test_supplied_origin_must_be_one_exact_http_origin(bad) -> None:
    sent, downstream = await invoke([(b"host", b"localhost:8710"), (b"origin", bad)])
    assert sent[0]["status"] == 403
    downstream.assert_not_called()


@pytest.mark.parametrize("header", [b"host", b"origin"])
async def test_duplicate_headers_are_refused_even_when_identical(header) -> None:
    headers = [(b"host", b"localhost:8710"), (b"origin", b"http://localhost:8710")]
    headers.append(next(pair for pair in headers if pair[0] == header))
    sent, downstream = await invoke(headers)
    assert sent[0]["status"] == 403
    downstream.assert_not_called()


async def test_omitted_ports_mean_protocol_default_and_vite_is_opt_in() -> None:
    sent, downstream = await invoke(
        [(b"host", b"localhost"), (b"origin", b"http://[::1]")], policy=AuthorityPolicy(80)
    )
    assert not sent
    downstream.assert_awaited_once()
    sent, downstream = await invoke([(b"host", b"localhost:5710")], policy=AuthorityPolicy(8710))
    assert sent[0]["status"] == 403
    downstream.assert_not_called()


@pytest.mark.parametrize("extension", [False, True])
@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"host", b"foreign:8710")],
        [
            (b"host", b"localhost:8710"),
            (b"origin", b"null"),
        ],
    ],
)
async def test_websocket_refusal_never_accepts(headers, extension) -> None:
    sent, downstream = await invoke(headers, kind="websocket", extension=extension)
    assert sent[0]["type"] == ("websocket.http.response.start" if extension else "websocket.close")
    if extension:
        assert sent[0]["status"] == 403
        assert ErrorResponse.model_validate_json(sent[1]["body"]).error.code == "forbidden"
    downstream.assert_not_called()


@pytest.mark.parametrize("kind", ["lifespan", "custom"])
async def test_other_scopes_pass_through(kind) -> None:
    sent, downstream = await invoke([], kind=kind)
    assert not sent
    downstream.assert_awaited_once()


async def test_allowed_websocket_passes_through() -> None:
    sent, downstream = await invoke(
        [
            (b"host", b"localhost:8710"),
            (b"origin", b"http://localhost:8710"),
        ],
        kind="websocket",
    )
    assert not sent
    downstream.assert_awaited_once()


async def test_sse_chunks_are_forwarded_before_the_stream_finishes() -> None:
    first = asyncio.Event()
    release = asyncio.Event()
    sent: list[Message] = []

    async def stream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"id: 1\n\n", "more_body": True})
        await release.wait()
        await send({"type": "http.response.body", "body": b"id: 2\n\n", "more_body": False})

    async def send(message):
        sent.append(message)
        if message.get("more_body"):
            first.set()

    scope: Any = {"type": "http", "headers": [(b"host", b"localhost:8710")]}
    async with asyncio.TaskGroup() as group:
        task = group.create_task(
            ApiGuard(stream, policy=AuthorityPolicy(8710))(scope, AsyncMock(), send)
        )
        try:
            await asyncio.wait_for(first.wait(), 1)
            assert sent[1]["body"] == b"id: 1\n\n" and not task.done()
        finally:
            release.set()


@pytest.mark.parametrize("replay", [False, True])
async def test_composition_refuses_before_handlers_or_model_calls(
    tmp_path: Path, replay: bool
) -> None:
    factory = AsyncMock(side_effect=AssertionError("model reached"))
    config = (
        ReplayConfig(log=REPO_ROOT / "fixtures/events/handwritten-level1.jsonl") if replay else None
    )
    app = create_app(Settings(runtime_dir=tmp_path / ".runtime"), config, client_factory=factory)
    # No lifespan: a refused request must not even ask a handler for its live resources.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://evil:8710"
    ) as client:
        for method, path in [
            ("POST", "/agents"),
            ("PUT", "/files"),
            ("GET", "/tree"),
            ("GET", "/"),
        ]:
            assert (await client.request(method, path)).status_code == 403
            assert (
                await client.request(
                    method,
                    path,
                    headers={
                        "host": "localhost:8710",
                        "origin": "http://evil:8710",
                    },
                )
            ).status_code == 403
    factory.assert_not_called()


@pytest.mark.parametrize(
    "value", ["0", "65536", "-1", "+1", "1.5", "bad", " 8710", "\uff18\uff17\uff11\uff10"]
)
@pytest.mark.parametrize("name", ["OVERSIGHT_PORT", "OVERSIGHT_VITE_PORT"])
def test_invalid_ports_fail_startup(tmp_path, monkeypatch, name, value) -> None:
    monkeypatch.delenv("OVERSIGHT_PORT", raising=False)
    monkeypatch.delenv("OVERSIGHT_VITE_PORT", raising=False)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        Settings.load(tmp_path / "missing")


def test_port_precedence_unset_defaults_and_equal_port_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OVERSIGHT_PORT", raising=False)
    monkeypatch.delenv("OVERSIGHT_VITE_PORT", raising=False)
    env_file = tmp_path / ".env"
    assert Settings.load(env_file).vite_port is None
    env_file.write_text("OVERSIGHT_PORT=9001\nOVERSIGHT_VITE_PORT=9002\n")
    assert (Settings.load(env_file).port, Settings.load(env_file).vite_port) == (9001, 9002)
    monkeypatch.setenv("OVERSIGHT_PORT", "9003")
    monkeypatch.setenv("OVERSIGHT_VITE_PORT", "")
    assert (Settings.load(env_file).port, Settings.load(env_file).vite_port) == (9003, None)
    assert resolve_ports({}, development=True) == (8710, 5710)
    monkeypatch.setenv("OVERSIGHT_VITE_PORT", "9003")
    with pytest.raises(ValueError, match="differ"):
        Settings.load(env_file)
    with pytest.raises(ValueError, match="differ"):
        Settings(port=9003, vite_port=9003)


async def test_forwarded_protocol_does_not_change_omitted_host_port() -> None:
    sent, downstream = await invoke(
        [(b"host", b"localhost"), (b"x-forwarded-proto", b"https")],
        policy=AuthorityPolicy(443),
    )
    assert sent[0]["status"] == 403
    downstream.assert_not_called()
    sent, downstream = await invoke(
        [(b"host", b"localhost"), (b"x-forwarded-proto", b"http")],
        scheme="https",
        policy=AuthorityPolicy(443),
    )
    assert not sent
    downstream.assert_awaited_once()


@pytest.mark.parametrize("command", ["serve", "replay"])
def test_cli_keeps_transport_scheme_independent_of_proxy_headers(monkeypatch, command) -> None:
    from oversight import __main__ as cli

    # No listener or provider: intercept the process boundary, checking both production entries.
    run = Mock()
    monkeypatch.setattr(cli.uvicorn, "run", run)
    monkeypatch.setattr(cli.Settings, "load", lambda: Settings())
    monkeypatch.setattr(cli, "create_app", Mock())
    args = ["oversight", command] + (["unused.jsonl"] if command == "replay" else [])
    monkeypatch.setattr("sys.argv", args)
    cli.main()
    run.assert_called_once()
    assert run.call_args.kwargs["proxy_headers"] is False
