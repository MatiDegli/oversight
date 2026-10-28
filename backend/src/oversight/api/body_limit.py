"""Admit an entire bounded HTTP body before any route can cause side effects."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from oversight.api.errors import error_response
from oversight.limits import MAX_BODY_BYTES


class BodyLimit:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Content-Length is only an early refusal; actual bytes are always counted too.
        for key, value in scope["headers"]:
            if key.lower() == b"content-length":
                digits = value.lstrip(b"0")
                if value.isdigit() and (
                    len(digits) > 7 or (digits and int(digits) > MAX_BODY_BYTES)
                ):
                    await self.refuse(scope, receive, send)
                    return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                await self.refuse(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        pending = True

        async def replay() -> Message:
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()  # preserve disconnect monitoring for SSE

        await self.app(scope, replay, send)

    async def refuse(self, scope: Scope, receive: Receive, send: Send) -> None:
        await error_response(
            "too_large", f"request body exceeds the {MAX_BODY_BYTES}-byte size limit"
        )(scope, receive, send)
