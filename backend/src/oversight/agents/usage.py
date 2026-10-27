"""Usage and spend: the cost of a call when it can be known, the run's ledger and brake, and the
ledger file that keeps the spend across runs."""

import asyncio
import contextlib
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast

from oversight.agents.client import Usage

type Brake = Literal["ok", "warn_50", "warn_80", "blocked"]


@dataclass(frozen=True)
class Pricing:
    """US dollars per million tokens."""

    input_usd: float
    output_usd: float
    cached_input_usd: float

    def cost(self, usage: Usage) -> float | None:
        """`None` when a count the price needs was not reported: unknown, never free."""
        if usage.input_tokens is None or usage.output_tokens is None:
            return None
        # Unreported cached tokens price as fresh input: an upper bound, never an undercount.
        cached = usage.cached_tokens or 0
        fresh = max(usage.input_tokens - cached, 0)
        total = (
            fresh * self.input_usd
            + cached * self.cached_input_usd
            + usage.output_tokens * self.output_usd
        )
        return total / 1_000_000


class UsageLedger:
    """Spend across the agents of a run. A call of unknown cost adds nothing it cannot know."""

    def __init__(
        self,
        budget_usd: float,
        spent_usd: float = 0.0,
        *,
        warn_at: tuple[float, float] = (0.5, 0.8),
        block_at: float = 0.9,
    ) -> None:
        self.budget_usd = budget_usd
        self.spent_usd = spent_usd
        self._warn_at = warn_at
        self._block_at = block_at

    def record(self, cost_usd: float | None) -> float:
        if cost_usd is not None:
            self.spent_usd += cost_usd
        return self.spent_usd

    @property
    def brake(self) -> Brake:
        if self.budget_usd <= 0:
            return "blocked"
        share = self.spent_usd / self.budget_usd
        if share > self._block_at:
            return "blocked"
        if share >= self._warn_at[1]:
            return "warn_80"
        if share >= self._warn_at[0]:
            return "warn_50"
        return "ok"


class LedgerError(Exception):
    """The ledger file cannot be read or written; starts on a metered profile fail closed."""


@dataclass(frozen=True)
class UnknownUsage:
    """A call whose usage the API did not report: its cost is unknown until the ledger is
    settled."""

    run_id: str
    agent_id: str
    model: str | None
    ts: str


@dataclass(frozen=True)
class LedgerState:
    spent_usd: float = 0.0
    unknown: tuple[UnknownUsage, ...] = ()


def _parse_ledger(text: str) -> LedgerState:
    data: object = json.loads(text)
    if not isinstance(data, dict):
        raise LedgerError("the ledger is not a JSON object")
    fields = cast(dict[str, object], data)
    spent = fields.get("spent_usd")
    raw_unknown = fields.get("unknown")
    if isinstance(spent, bool) or not isinstance(spent, int | float) or not math.isfinite(spent):
        raise LedgerError("the ledger's spent_usd is not a number")
    if spent < 0 or not isinstance(raw_unknown, list):
        raise LedgerError("the ledger's spent_usd is negative or its unknown is not a list")
    unknown: list[UnknownUsage] = []
    for raw in cast(list[object], raw_unknown):
        if not isinstance(raw, dict):
            raise LedgerError("an unknown entry of the ledger is not an object")
        entry = cast(dict[str, object], raw)
        run_id, agent_id, model, ts = (entry.get(k) for k in ("run_id", "agent_id", "model", "ts"))
        if not (isinstance(run_id, str) and isinstance(agent_id, str) and isinstance(ts, str)):
            raise LedgerError("an unknown entry of the ledger lacks run_id, agent_id or ts")
        unknown.append(
            UnknownUsage(run_id, agent_id, model if isinstance(model, str) else None, ts)
        )
    return LedgerState(float(spent), tuple(unknown))


class LedgerFile:
    """`.runtime/ledger.json`, `{spent_usd, unknown: [{run_id, agent_id, model, ts}]}`: the spend of
    every metered run, kept across restarts and resets. Writes are atomic (a temp file in the same
    directory, fsync, `os.replace`), run in a thread, one at a time."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self.state: LedgerState | None = None
        """What `load` read and `record` wrote; `None` until loaded, or once a write failed."""

    def read(self) -> LedgerState:
        """Synchronous. A missing file is nothing spent; an unreadable or malformed one raises
        `LedgerError`."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return LedgerState()
        except (OSError, UnicodeDecodeError) as error:
            raise LedgerError(f"the ledger cannot be read: {error.__class__.__name__}") from error
        try:
            return _parse_ledger(text)
        except json.JSONDecodeError as error:
            raise LedgerError("the ledger is not JSON") from error

    def write(self, state: LedgerState) -> None:
        """Synchronous and atomic: a failure before the replace leaves the old file as it was."""
        body = {
            "spent_usd": state.spent_usd,
            "unknown": [asdict(entry) for entry in state.unknown],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".ledger-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(body, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp)
            raise

    async def load(self) -> LedgerState:
        """`read` in a thread, kept as `state`; `LedgerError` leaves `state` as `None`."""
        self.state = None
        self.state = await asyncio.to_thread(self.read)
        return self.state

    async def record(self, cost_usd: float | None, unknown: UnknownUsage) -> LedgerState:
        """One call, written before it returns: a known cost adds to `spent_usd`, an unknown one
        (`None`) appends `unknown`. A failed write raises and leaves `state` as `None`, so the
        caller refuses further metered starts: the spend on disk is behind."""
        async with self._lock:
            state = self.state
            if state is None:
                raise LedgerError("the ledger was not loaded, or an earlier write failed")
            if cost_usd is None:
                new = LedgerState(state.spent_usd, (*state.unknown, unknown))
            else:
                new = LedgerState(state.spent_usd + cost_usd, state.unknown)
            self.state = None
            await asyncio.to_thread(self.write, new)
            self.state = new
            return new
