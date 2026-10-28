"""Opt-in local evaluation timing. Only explicit, body-free fields may reach JSONL.

Start the server with OVERSIGHT_EVAL_TRACE_DIR pointing at the evaluator's evidence
folder; tools/eval --trace supplies an opaque attempt header. Neither switch alone
traces a product run. Nested spans are inclusive; parent_id prevents double counting.
Clocks are host monotonic seconds (the evaluator and server run on the same host).
"""

import contextlib
import json
import logging
import os
import re
import time
from collections.abc import Generator
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast
from uuid import uuid4

TRACE_HEADER = "x-oversight-eval-trace"
_FIELDS = frozenset(
    [
        "run",
        "attempt",
        "agent",
        "turn",
        "http_attempt",
        "tool_call",
        "requested_model",
        "reported_model",
        "reasoning_effort",
        "prompt_version",
        "output_allowance",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "request_bytes",
        "observation_bytes",
        "finish_reason",
        "tool_count",
        "http_status",
        "request_id",
        "rate_limits",
        "event_seq",
        "lock_kind",
        "state",
    ]
)
_SAFE = re.compile(r"[a-zA-Z0-9_.:/ +,=;-]{1,200}\Z")


@dataclass(frozen=True)
class Observer:
    path: Path
    ids: dict[str, object] = field(default_factory=lambda: dict[str, object]())
    parent: str | None = None
    source: str = "server"

    def write(self, name: str, start: float, end: float, **fields: object) -> None:
        record = {
            "source": self.source,
            "name": name,
            "start": start,
            "end": end,
            "parent_id": self.parent,
            **self.ids,
            **{k: v for k, v in fields.items() if k in _FIELDS or k == "span_id"},
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # One bounded append per completed span; no prompt/body/exception serialization.
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        except OSError:
            logging.getLogger(__name__).warning("evaluation timing could not be written")


CURRENT: ContextVar[Observer | None] = ContextVar("evaluation_timing", default=None)


@contextlib.contextmanager
def bind(**fields: object) -> Generator[None]:
    observer = CURRENT.get()
    token = CURRENT.set(
        replace(observer, ids=observer.ids | {k: v for k, v in fields.items() if k in _FIELDS})
        if observer
        else None
    )
    try:
        yield
    finally:
        CURRENT.reset(token)


def annotate(**fields: object) -> None:
    if observer := CURRENT.get():
        CURRENT.set(
            replace(observer, ids=observer.ids | {k: v for k, v in fields.items() if k in _FIELDS})
        )


@contextlib.contextmanager
def span(name: str, **fields: object) -> Generator[dict[str, object]]:
    observer = CURRENT.get()
    values = dict(fields)
    if observer is None:
        yield values
        return
    started = time.monotonic()
    span_id = uuid4().hex
    token = CURRENT.set(replace(observer, parent=span_id))
    try:
        yield values
    finally:
        ended = time.monotonic()
        CURRENT.reset(token)
        observer.write(name, started, ended, span_id=span_id, **values)


def mark(name: str, **fields: object) -> None:
    if observer := CURRENT.get():
        now = time.monotonic()
        observer.write(name, now, now, **fields)


@contextlib.contextmanager
def evaluation_trace(run_id: str, attempt_id: str | None) -> Generator[None]:
    directory = os.environ.get("OVERSIGHT_EVAL_TRACE_DIR")
    observer = None
    if directory and attempt_id and re.fullmatch(r"[a-f0-9]{32}", attempt_id):
        # The HTTP peer never supplies a filesystem path. The operator owns the directory.
        observer = Observer(
            Path(directory) / f"timing-{attempt_id}.jsonl", {"run": run_id, "attempt": attempt_id}
        )
    token = CURRENT.set(observer)
    try:
        yield
    finally:
        CURRENT.reset(token)


def response_fields(status: int, headers: object) -> dict[str, object]:
    # Only explicitly selected provider metadata; never Authorization, cookies or body text.
    from collections.abc import Mapping

    if not isinstance(headers, Mapping):
        return {"http_status": status}
    selected: dict[str, str] = {}
    request_id = None
    for key, value in cast(Mapping[object, object], headers).items():
        if not isinstance(key, str) or not isinstance(value, str) or not _SAFE.fullmatch(value):
            continue
        key = key.lower()
        if key in {"x-request-id", "request-id", "x-meta-request-id", "x-baseten-request-id"}:
            request_id = value
        if key in {"retry-after", "ratelimit-limit", "ratelimit-remaining", "ratelimit-reset"} or (
            key.startswith("x-ratelimit-") and re.fullmatch(r"[a-z-]{1,80}", key)
        ):
            selected[key] = value
    return {"http_status": status, "request_id": request_id, "rate_limits": selected}
