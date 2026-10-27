"""One loopback authority policy for HTTP and WebSocket traffic, before routing."""

import re
from dataclasses import dataclass

from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket

from oversight.api.errors import error_response

_AUTHORITY = re.compile(rb"(localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?")


def _authority(value: bytes, default_port: int) -> tuple[bytes, int] | None:
    match = _AUTHORITY.fullmatch(value)
    if match is None:
        return None
    port = int(match[2]) if match[2] else default_port
    return (match[1], port) if 1 <= port <= 65535 else None


@dataclass(frozen=True)
class AuthorityPolicy:
    """Only these literal loopback hosts at the effective backend and optional Vite ports.

    A terminal may consume this policy and add same-authority/authentication requirements;
    it must not define another allow list. Forwarded headers never participate.
    """

    port: int
    vite_port: int | None = None

    def refusal(self, scope: Scope) -> str | None:
        ports = {self.port, self.vite_port}
        hosts = [v for k, v in scope["headers"] if k.lower() == b"host"]
        default = 443 if scope.get("scheme") in ("https", "wss") else 80
        host = _authority(hosts[0], default) if len(hosts) == 1 else None
        if host is None or host[1] not in ports:
            return "Host"
        origins = [v for k, v in scope["headers"] if k.lower() == b"origin"]
        if not origins:  # CLI tools and eval clients send no Origin, including on writes.
            return None
        origin = None
        if len(origins) == 1 and origins[0].startswith(b"http://"):
            origin = _authority(origins[0][7:], 80)
        if origin is None or origin[1] not in ports:
            return "Origin"
        return None


class ApiGuard:
    """Pure ASGI: no body reads, response buffering, CORS, or route exemptions."""

    def __init__(self, app: ASGIApp, *, policy: AuthorityPolicy) -> None:
        self.app = app
        self.policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        refused = self.policy.refusal(scope)
        if refused is None:
            await self.app(scope, receive, send)
            return
        response = error_response(
            "forbidden",
            f"{refused} is not allowed; check OVERSIGHT_PORT and OVERSIGHT_VITE_PORT.",
        )
        if scope["type"] == "http":
            await response(scope, receive, send)
        else:
            socket = WebSocket(scope, receive, send)
            if "websocket.http.response" in scope.get("extensions", {}):
                await socket.send_denial_response(response)
            else:
                await socket.close(code=1008)
