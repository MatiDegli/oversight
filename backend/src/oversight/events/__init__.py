"""The event store, its single writer, subscribers, replay and export.

Other modules import only what this file exports; everything else here is internal.
"""

from pathlib import Path

from oversight.events.clock import Clock, format_ts, parse_ts, system_clock
from oversight.events.jsonl import LogError, export_run, read_jsonl, write_jsonl
from oversight.events.needs_you import (
    NEEDS_YOU_CODES,
    NeedsYou,
    format_needs_you,
    parse_needs_you,
)
from oversight.events.paths import absolute_paths
from oversight.events.player import MAX_GAP_S, Sleep, play, replay_delays
from oversight.events.references import validate_cross_review_limit
from oversight.events.store import (
    EVENT_TYPES,
    EventPayload,
    EventRejected,
    EventStore,
    StoreClosedError,
    StoredEvent,
    read_store,
)


def run_store_path(runtime_dir: Path, run_id: str) -> Path:
    """Where a run's log lives: `.runtime/runs/{run_id}.sqlite`."""
    return runtime_dir / "runs" / f"{run_id}.sqlite"


__all__ = [
    "EVENT_TYPES",
    "MAX_GAP_S",
    "NEEDS_YOU_CODES",
    "Clock",
    "EventPayload",
    "EventRejected",
    "EventStore",
    "LogError",
    "NeedsYou",
    "Sleep",
    "StoreClosedError",
    "StoredEvent",
    "absolute_paths",
    "export_run",
    "format_needs_you",
    "format_ts",
    "parse_needs_you",
    "parse_ts",
    "play",
    "read_jsonl",
    "read_store",
    "replay_delays",
    "run_store_path",
    "system_clock",
    "validate_cross_review_limit",
    "write_jsonl",
]
