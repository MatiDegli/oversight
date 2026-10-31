"""Usage and spend: the cost of a call when it can be known, the run's ledger and brake, and the
ledger file that keeps the spend across runs."""

import asyncio
import contextlib
import json
import logging
import math
import os
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast

from oversight.agents.client import Usage
from oversight.limits import MAX_FILE_BYTES, SizeLimitError, bounded_text
from oversight.telemetry import span
from oversight_contracts.events import UsageTick

_log = logging.getLogger(__name__)

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
        braked: bool = True,
    ) -> None:
        self.budget_usd = budget_usd
        self.spent_usd = spent_usd
        self._warn_at = warn_at
        self._block_at = block_at
        self._braked = braked
        """`False` for a profile the server never brakes (the fake model): its brake reads `ok`
        whatever the budget, since nothing is refused."""

    def record(self, cost_usd: float | None) -> float:
        if cost_usd is not None:
            self.spent_usd += cost_usd
        return self.spent_usd

    @property
    def brake(self) -> Brake:
        if not self._braked:
            return "ok"
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
        self.loaded = False
        """Whether a load succeeded; `writing` distinguishes an in-flight write from loss."""
        self.writing = False
        self.change_revision = 0
        self._watchers: dict[object, Callable[[], None]] = {}

    def watch(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Loop-owned notifications; unsubscribe is idempotent."""
        key = object()
        self._watchers[key] = callback

        def unsubscribe() -> None:
            self._watchers.pop(key, None)

        return unsubscribe

    def notify_loss(self) -> None:
        """Announce uncertainty, including a caller's sticky loss after durable I/O."""
        self._changed()

    def _changed(self) -> None:
        self.change_revision += 1
        for callback in tuple(self._watchers.values()):
            try:
                callback()
            except Exception:
                _log.exception("Ledger change observer failed")

    def _install(self, state: LedgerState) -> None:
        self.state = state
        self.writing = False
        self._changed()

    async def wait_changed(self, after: int, *, deadline: float) -> None:
        """Wait for a newer revision at an absolute loop deadline; never cancel a writer."""
        changed = asyncio.Event()
        unsubscribe = self.watch(changed.set)
        try:
            async with asyncio.timeout_at(deadline):
                while self.change_revision <= after:
                    changed.clear()
                    await changed.wait()
        finally:
            unsubscribe()

    def read(self) -> LedgerState:
        """Synchronous. A missing file is nothing spent; an unreadable or malformed one raises
        `LedgerError`."""
        try:
            text = bounded_text(self.path)
        except FileNotFoundError:
            return LedgerState()
        except SizeLimitError as error:
            raise LedgerError(str(error)) from error
        except (OSError, UnicodeDecodeError) as error:
            raise LedgerError(
                f"{self.path.name} cannot be read: {error.__class__.__name__}"
            ) from error
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
        text = json.dumps(body, separators=(",", ":")) + "\n"
        if len(text.encode("utf-8")) > MAX_FILE_BYTES:
            raise LedgerError(
                f"{self.path.name} exceeds the {MAX_FILE_BYTES}-byte size limit; "
                "settle the ledger before recording more unknown usage"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".ledger-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
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
        try:
            state = await asyncio.to_thread(self.read)
        except BaseException:
            self.writing = False
            self.notify_loss()
            raise
        self.loaded = True
        self._install(state)
        return state

    async def record(self, cost_usd: float | None, unknown: UnknownUsage) -> LedgerState:
        """One call, written before it returns: a known cost adds to `spent_usd`, an unknown one
        (`None`) appends `unknown`. A failed write raises and leaves `state` as `None`, so the
        caller refuses further metered starts: the spend on disk is behind. The write runs to its
        end before the lock is released, also when this call is cancelled: a thread cannot be
        stopped, and a late `os.replace` must not land on a file someone else has written since.
        A cancelled call whose write then failed still raises its `CancelledError`, with the
        write's error as `__cause__`: the caller must tell a lost write from a clean cancel."""
        with span("ledger_lock_wait"):
            await self._lock.acquire()
        try:
            state = self.state
            if state is None:
                raise LedgerError("the ledger was not loaded, or an earlier write failed")
            if cost_usd is None:
                new = LedgerState(state.spent_usd, (*state.unknown, unknown))
            else:
                new = LedgerState(state.spent_usd + cost_usd, state.unknown)
            self.writing = True
            self.state = None

            async def persist() -> None:
                with span("ledger_write"):
                    await asyncio.to_thread(self.write, new)

            write = asyncio.ensure_future(persist())
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError as cancelled:
                error = await self._drain(write, new)
                if error is not None:
                    raise cancelled from error
                raise
            except BaseException:
                self.writing = False
                self.notify_loss()
                raise
            self._install(new)
            return new
        finally:
            self._lock.release()

    async def _drain(self, write: "asyncio.Future[None]", new: LedgerState) -> BaseException | None:
        """Wait for a write in flight however often this task is cancelled again; the error it
        ended with, or `None`. `state` is `new` only if the write succeeded; otherwise it stays
        `None`, and the next metered start is refused."""
        while not write.done():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait({write})
        if write.cancelled():
            self.writing = False
            self.notify_loss()
            return None
        error = write.exception()
        if error is None:
            self._install(new)
        else:
            self.writing = False
            self.notify_loss()
            _log.error("a cancelled ledger write failed: %s", error.__class__.__name__)
        return error


def validate_usage_tick(tick: "UsageTick") -> None:
    """Require call purpose and the corresponding accounting identity in live producers."""
    if tick.role is None:
        raise ValueError("live usage requires a role")
    if tick.role in {"agent", "review"} and tick.change_id is None:
        raise ValueError("agent and review usage require a change")
    if tick.role == "plan" and tick.plan_id is None:
        raise ValueError("plan usage requires a plan")
    if any(
        value is not None and value < 0
        for value in (tick.input_tokens, tick.output_tokens, tick.cached_tokens, tick.cost_usd)
    ):
        raise ValueError("usage cannot be negative")
