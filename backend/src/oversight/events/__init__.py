"""The event store, its single writer, subscribers, replay and export.

Other modules import only what this file exports; everything else here is internal.
"""

from pathlib import Path

from oversight.events.activity_fold import (
    ActivityFold,
    ActivityGroup,
    ActivityInterval,
    ActorActivity,
    fold_activity,
)
from oversight.events.clock import Clock, format_ts, parse_ts, system_clock
from oversight.events.history import (
    HistoryTooLarge,
    InvalidHistoryRequest,
    read_history,
    resolution_at,
)
from oversight.events.jsonl import LogError, export_run, read_jsonl, write_jsonl
from oversight.events.list_prices import (
    LIST_PRICES,
    ListPrice,
    ListPriceFold,
    ListPriceGroup,
    PriceTable,
    price_usage,
)
from oversight.events.needs_you import (
    NEEDS_YOU_CODES,
    NEEDS_YOU_TEMPLATE,
    NeedsYou,
    NeedsYouWait,
    TerminalClaim,
    TerminalClaims,
    format_needs_you,
    needs_you_golden,
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
from oversight.events.supervision_fold import (
    AgentSupervision,
    ChangeSupervision,
    Decisions,
    NotMerged,
    Outcomes,
    Spend,
    SupervisionFold,
    SupervisionTotals,
    fold_supervision,
)
from oversight.events.usage_fold import (
    ChangeAccounting,
    RunUsage,
    UnassignedUsage,
    UsageFold,
    fold_usage,
)


def run_store_path(runtime_dir: Path, run_id: str) -> Path:
    """Where a run's log lives: `.runtime/runs/{run_id}.sqlite`."""
    return runtime_dir / "runs" / f"{run_id}.sqlite"


__all__ = [
    "EVENT_TYPES",
    "LIST_PRICES",
    "MAX_GAP_S",
    "NEEDS_YOU_CODES",
    "NEEDS_YOU_TEMPLATE",
    "ActivityFold",
    "ActivityGroup",
    "ActivityInterval",
    "ActorActivity",
    "AgentSupervision",
    "ChangeAccounting",
    "ChangeSupervision",
    "Clock",
    "Decisions",
    "EventPayload",
    "EventRejected",
    "EventStore",
    "HistoryTooLarge",
    "InvalidHistoryRequest",
    "ListPrice",
    "ListPriceFold",
    "ListPriceGroup",
    "LogError",
    "NeedsYou",
    "NeedsYouWait",
    "NotMerged",
    "Outcomes",
    "PriceTable",
    "RunUsage",
    "Sleep",
    "Spend",
    "StoreClosedError",
    "StoredEvent",
    "SupervisionFold",
    "SupervisionTotals",
    "TerminalClaim",
    "TerminalClaims",
    "UnassignedUsage",
    "UsageFold",
    "absolute_paths",
    "export_run",
    "fold_activity",
    "fold_supervision",
    "fold_usage",
    "format_needs_you",
    "format_ts",
    "needs_you_golden",
    "parse_needs_you",
    "parse_ts",
    "play",
    "price_usage",
    "read_history",
    "read_jsonl",
    "read_store",
    "replay_delays",
    "resolution_at",
    "run_store_path",
    "system_clock",
    "validate_cross_review_limit",
    "write_jsonl",
]
