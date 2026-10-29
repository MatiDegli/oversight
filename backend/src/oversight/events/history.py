"""Bounded history of the current run, read independently of the writer and live state."""

import asyncio
import json
import sqlite3
from contextlib import closing

from oversight.events.store import EventStore
from oversight_contracts.commands import HistoryPage

HISTORY_MAX_BYTES = 1024 * 1024
HISTORY_TYPES = (
    "change.proposed",
    "change.assessed",
    "change.reviewed",
    "change.facts_recorded",
    "tests.executed",
    "change.resolved",
    "revert.resolved",
    "merge_mode.changed",
    "plan.updated",
    "queue.updated",
    "lead.status",
)


class HistoryTooLarge(ValueError):
    """The first eligible envelope cannot fit intact in a success body."""


class InvalidHistoryRequest(ValueError):
    """A cursor or count is outside the committed history bounds."""


async def read_history(
    store: EventStore,
    *,
    after_seq: int = 0,
    limit: int = 50,
    through_seq: int | None = None,
) -> bytes:
    """Return a validated, serialized HistoryPage, preserving the original envelope JSON.

    Capture the memory-confirmed SQL horizon before the first await. No pending insertion,
    including one committed on disk but not yet acknowledged by the writer, can enter it.
    """
    committed = store.last_seq
    horizon = committed if through_seq is None else through_seq
    if not 1 <= limit <= 100 or not 0 <= after_seq <= horizon <= committed:
        raise InvalidHistoryRequest(
            "require 1 <= limit <= 100 and 0 <= after_seq <= through_seq <= last committed seq"
        )
    return await asyncio.to_thread(_read_page, store, after_seq, limit, horizon)


def _suffix(next_seq: int | None) -> bytes:
    return b'],"next_seq":' + (str(next_seq).encode() if next_seq is not None else b"null") + b"}"


def _read_page(store: EventStore, after: int, limit: int, horizon: int) -> bytes:
    prefix = f'{{"run_id":{json.dumps(store.run_id)},"through_seq":{horizon},"events":['.encode()
    envelopes: list[bytes] = []
    size = len(prefix)
    next_seq = None
    # Separate read-only connection: WAL readers never wait for the writer's transaction.
    with closing(sqlite3.connect(f"{store.path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        placeholders = ",".join("?" for _ in HISTORY_TYPES)
        rows: list[tuple[int, int]] = db.execute(
            "SELECT seq, length(CAST(envelope AS BLOB)) FROM events "
            f"WHERE seq > ? AND seq <= ? AND type IN ({placeholders}) ORDER BY seq LIMIT ?",
            (after, horizon, *HISTORY_TYPES, limit + 1),
        ).fetchall()
        for index, (seq, length) in enumerate(rows[:limit]):
            cursor = seq if index + 1 < len(rows) else None
            candidate_size = size + bool(envelopes) + length
            # Inspect byte lengths before loading payloads, including the lookahead record.
            if candidate_size + len(_suffix(cursor)) > HISTORY_MAX_BYTES:
                if not envelopes:
                    raise HistoryTooLarge(
                        "the first history envelope exceeds the 1 MiB page budget"
                    )
                break
            (envelope,) = db.execute("SELECT envelope FROM events WHERE seq = ?", (seq,)).fetchone()
            envelopes.append(envelope.encode("utf-8"))
            size = candidate_size
            next_seq = cursor
    body = prefix + b",".join(envelopes) + _suffix(next_seq)
    HistoryPage.model_validate_json(body)
    return body
