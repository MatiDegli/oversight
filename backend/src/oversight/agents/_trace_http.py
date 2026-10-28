"""Measure SDK preparation separately from the non-streaming HTTP exchange."""

import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import uuid4

import httpx2 as httpx

from oversight.telemetry import CURRENT, Observer


@dataclass
class Exchange:
    observer: Observer
    started: float = field(default_factory=time.monotonic)
    sent: float | None = None
    fields: dict[str, object] = field(
        default_factory=lambda: {
            "http_status": None,
            "request_id": None,
            "rate_limits": {},
            "request_bytes": None,
        }
    )


_EXCHANGE: ContextVar[Exchange | None] = ContextVar("traced_http_exchange", default=None)


async def request_ready(request: httpx.Request) -> None:
    if exchange := _EXCHANGE.get():
        exchange.sent = time.monotonic()
        exchange.fields["request_bytes"] = len(request.content)


@contextmanager
def exchange_span() -> Generator[dict[str, object]]:
    observer = CURRENT.get()
    if observer is None:
        yield {}
        return
    exchange = Exchange(observer)
    token = _EXCHANGE.set(exchange)
    try:
        yield exchange.fields
    finally:
        ended = time.monotonic()
        _EXCHANGE.reset(token)
        observer.write(
            "request_preparation", exchange.started, exchange.sent or ended, span_id=uuid4().hex
        )
        if exchange.sent is not None:
            observer.write(
                "http_attempt", exchange.sent, ended, span_id=uuid4().hex, **exchange.fields
            )
