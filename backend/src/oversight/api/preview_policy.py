"""Preview response privacy and exact application frame destinations."""

import re

from starlette.types import Message, Scope, Send


def is_preview(scope: Scope) -> bool:
    return str(scope.get("path", "")).lstrip("/").partition("/")[0] == "previews"


def private_send(scope: Scope, send: Send) -> Send:
    """Wrap before admission, so guard/body/router refusals carry the same cache policy."""

    async def private(message: Message) -> None:
        if message["type"] == "http.response.start" and is_preview(scope):
            headers = [
                (k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"
            ]
            message = {**message, "headers": [*headers, (b"cache-control", b"no-store")]}
        if message["type"] == "http.response.start":
            headers: list[tuple[bytes, bytes]] = []
            for key, value in message.get("headers", []):
                if key.lower() == b"content-security-policy":
                    value = request_frame_policy(value.decode("latin-1"), scope).encode("latin-1")
                headers.append((key, value))
            message = {**message, "headers": headers}
        await send(message)

    return private


def frame_policy(
    ports: tuple[int, ...] = (),
    *,
    replay: bool = False,
    reserved: tuple[int | None, ...] = (),
) -> str:
    """Origins are embedding authorities, never frame destinations. Empty/replay refuses all."""
    if replay or not ports:
        return "frame-src 'none'"
    if len(set(ports)) != len(ports) or any(
        type(p) is not int or not 1 <= p <= 65535 or p in reserved for p in ports
    ):
        raise ValueError("invalid preview port pool")
    return "frame-src " + " ".join(f"http://127.0.0.1:{p}" for p in ports)


def request_frame_policy(policy: str, scope: Scope) -> str:
    """Resolve the parent policy after API authority admission, without forwarded headers."""
    hosts = [v for k, v in scope.get("headers", []) if k.lower() == b"host"]
    match = (
        re.fullmatch(rb"(localhost|127\.0\.0\.1)(?::[1-9][0-9]{0,4})?", hosts[0])
        if len(hosts) == 1
        else None
    )
    frame = "localhost" if match and match[1] == b"127.0.0.1" else "127.0.0.1"
    directives: list[str] = []
    for directive in policy.split(";"):
        words = directive.split()
        if words and words[0] == "frame-src":
            if (
                match is None
                or len(words) < 2
                or not all(
                    re.fullmatch(r"http://(?:localhost|127\.0\.0\.1):[1-9][0-9]{0,4}", word)
                    for word in words[1:]
                )
            ):
                directive = "frame-src 'none'"
            else:
                directive = "frame-src " + " ".join(
                    f"http://{frame}:" + word.rsplit(":", 1)[1] for word in words[1:]
                )
        directives.append(directive.strip())
    return "; ".join(directives)
