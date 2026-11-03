"""Preview response privacy and exact application frame destinations."""

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
