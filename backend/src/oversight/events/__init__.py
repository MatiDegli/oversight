"""The event store, its single writer, subscribers, replay and export.

Other modules import only what this file exports; everything else here is internal.
"""

from pathlib import Path

from oversight.events.clock import Clock, format_ts, parse_ts, system_clock
from oversight.events.jsonl import LogError, export_run, read_jsonl, write_jsonl
from oversight.events.paths import absolute_paths
from oversight.events.player import MAX_GAP_S, Sleep, play, replay_delays
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
    "Clock",
    "EventPayload",
    "EventRejected",
    "EventStore",
    "LogError",
    "Sleep",
    "StoreClosedError",
    "StoredEvent",
    "absolute_paths",
    "export_run",
    "format_ts",
    "parse_ts",
    "play",
    "read_jsonl",
    "read_store",
    "replay_delays",
    "run_store_path",
    "system_clock",
    "write_jsonl",
]
