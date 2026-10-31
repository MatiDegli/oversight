"""Durable writes shared by author and review; review failures never halt the author."""

# Runtime composition helpers access LiveRun's owned ledger and event store.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from oversight.agents import (
    DecisionInput,
    DecisionUnavailable,
    LedgerError,
    LedgerFile,
    LedgerState,
    ModelReply,
    Pricing,
    UnknownUsage,
    UsageLedger,
    validate_usage_tick,
)
from oversight.events import format_needs_you, format_ts
from oversight.settings import positive_amount
from oversight_contracts.events import ModelProfile, UsageTick
from oversight_contracts.review import ChangeTarget

if TYPE_CHECKING:
    from oversight.app import LiveRun


async def durable[T](
    work: Awaitable[T],
    *,
    wait: Callable[..., Awaitable[object]] = asyncio.wait,
) -> T:
    """Drain before propagating cancellation; retain a failed write as its cause."""
    task = asyncio.ensure_future(work)
    cancel: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await wait({task})
        except asyncio.CancelledError as error:
            cancel = error
    try:
        result = task.result()
    except BaseException as error:
        if cancel is not None:
            raise cancel from error
        raise
    if cancel is not None:
        raise cancel
    return result


async def write_tick(
    live: LiveRun, tick: UsageTick, *, wait: Callable[..., Awaitable[object]] = asyncio.wait
) -> LedgerState:
    entry = UnknownUsage(live.run_id, tick.agent_id, tick.model, format_ts(datetime.now(UTC)))
    return await durable(live.ledger.record(tick.cost_usd, entry), wait=wait)


class ReviewLedgerFailure(Exception):
    """This review's paid attempt could not be recorded; no author state is implicated."""


class PlanAccounting:
    """A planner receipt belongs to its plan, never to an author or a slot's provider."""

    def __init__(self, live: LiveRun, plan_id: str, profile: ModelProfile, agent_id: str) -> None:
        self.live, self.plan_id, self.profile, self.agent_id = live, plan_id, profile, agent_id
        self.failure: str | None = None

    async def record(self, reply: ModelReply) -> None:
        await durable(self._record(reply))

    async def _record(self, reply: ModelReply) -> None:
        live = self.live
        metered = self.profile.provider == "meta"
        price = live._price_for(self.profile)
        cost = price.cost(reply.usage) if price is not None else None
        spent = live._usage.record(cost) if metered else live._usage.spent_usd
        tick = UsageTick(
            agent_id=self.agent_id,
            plan_id=self.plan_id,
            change_id=None,
            role="plan",
            model=reply.model,
            input_tokens=reply.usage.input_tokens,
            output_tokens=reply.usage.output_tokens,
            cached_tokens=reply.usage.cached_tokens,
            cost_usd=cost,
            spent_usd=spent,
            budget_usd=live._usage.budget_usd,
            brake=live._usage.brake if metered else "ok",
        )
        validate_usage_tick(tick)
        state = live.ledger.state
        if metered:
            try:
                state = await write_tick(live, tick)
            except (asyncio.CancelledError, LedgerError, OSError) as error:
                cause = error.__cause__ if isinstance(error, asyncio.CancelledError) else error
                if not isinstance(cause, LedgerError | OSError):
                    raise
                live._ledger_lost = True
                live.ledger.notify_loss()
                self.failure = f"Plan {self.plan_id} ledger write failed ({type(cause).__name__})."
                raise LedgerError(self.failure) from cause
        if state is not None:
            tick = tick.model_copy(update={"spent_usd": state.spent_usd})
        # The planner has no agent record: _publish would use the wrong provider ownership.
        receipt = await live.store.publish(tick)
        if live._manager is not None:
            live._manager.usage_recorded(receipt)
            if state is not None:
                live._manager.spend_received(receipt)

    async def report_failure(self) -> None:
        assert self.failure is not None
        reason = (
            f"Plan {self.plan_id}; model={self.profile.model}; provider={self.profile.provider}. "
            f"{self.failure} "
            "Stop the server and settle with Meta's console total before the next run: "
            "uv run python -m oversight ledger settle --spent-usd X."
        )
        await self.live._finish(
            self.live._report("server", reason, agent_id=self.agent_id, fatal=True)
        )


class BasetenDecisionAccounting:
    """A separate, persistent decision-call balance, never the Meta credits ledger.

    RuntimeDecisions serializes logical calls through accounting, so concurrent candidates cannot
    both enter against an old balance. Existing author and cross-review metering is unchanged.
    Missing prices/budget, unknown usage and any receipt failure close decision admission.
    A failed receipt leaves a recovery marker: restart cannot silently trust the old total.
    The maintainer must reconcile the provider bill and receipt before removing that marker.
    """

    def __init__(self, live: LiveRun) -> None:
        self.live = live
        self.ledger = LedgerFile(live._settings.runtime_dir / "baseten-decisions-ledger.json")
        self._recovery = self.ledger.path.with_suffix(".unreliable")
        self._loaded = False
        self._lost = False
        settings = live._settings
        self.budget = positive_amount(str(settings.baseten_budget_usd))
        input_price = positive_amount(str(settings.baseten_input_usd_per_m))
        output_price = positive_amount(str(settings.baseten_output_usd_per_m))
        self.pricing = (
            Pricing(input_price, output_price, input_price)
            if input_price is not None and output_price is not None
            else None
        )

    async def load(self) -> None:
        if not self._loaded:
            self._loaded = True
            try:
                if self._recovery.exists():
                    self._lost = True
                    return
                await self.ledger.load()
            except (LedgerError, OSError):
                self._lost = True

    async def admit(self) -> None:
        """No suspension between checking the current ledger and returning transport admission."""
        if self.live._closing:
            raise DecisionUnavailable("closed")
        if self.budget is None:
            raise DecisionUnavailable("budget_unconfigured")
        if self.pricing is None:
            raise DecisionUnavailable("pricing_unconfigured")
        state = self.ledger.state
        if self._lost or state is None:
            raise DecisionUnavailable("ledger_unreliable")
        if state.unknown:
            raise DecisionUnavailable("ledger_unknown")
        if UsageLedger(self.budget, state.spent_usd).brake == "blocked":
            raise DecisionUnavailable("budget_blocked")

    async def record(self, input: DecisionInput, reply: ModelReply) -> None:
        await durable(self._record(input, reply))

    async def _record(self, input: DecisionInput, reply: ModelReply) -> None:
        assert self.budget is not None and self.pricing is not None
        cost = self.pricing.cost(reply.usage)
        entry = UnknownUsage(
            self.live.run_id, input.agent_id, reply.model, format_ts(datetime.now(UTC))
        )
        try:
            state = await self.ledger.record(cost, entry)
            tick = UsageTick(
                agent_id=input.agent_id,
                change_id=input.target.change_id,
                role="review",
                model=reply.model,
                input_tokens=reply.usage.input_tokens,
                output_tokens=reply.usage.output_tokens,
                cached_tokens=reply.usage.cached_tokens,
                cost_usd=cost,
                spent_usd=state.spent_usd,
                budget_usd=self.budget,
                brake="blocked"
                if state.unknown
                else UsageLedger(self.budget, state.spent_usd).brake,
            )
            validate_usage_tick(tick)
            # Publish directly: LiveRun._publish meters according to the author's provider.
            receipt = await self.live.store.publish(tick)
            if self.live._manager is not None:
                self.live._manager.usage_recorded(receipt)
        except (Exception, asyncio.CancelledError) as error:
            self._lost = True
            # Preserve the failed receipt where possible and close admission across restart.
            # A partial marker also blocks: it must never be interpreted as a settled total.
            recovery = json.dumps(
                {
                    "run_id": self.live.run_id,
                    "agent_id": input.agent_id,
                    "change_id": input.target.change_id,
                    "revision": input.target.revision,
                    "node_id": input.difference.node_id,
                    "model": reply.model,
                    "usage": {
                        "input_tokens": reply.usage.input_tokens,
                        "output_tokens": reply.usage.output_tokens,
                        "cached_tokens": reply.usage.cached_tokens,
                    },
                    "cost_usd": cost,
                }
            )
            # The process remains closed even if storage itself is unavailable.
            with contextlib.suppress(OSError):
                await asyncio.to_thread(self._save_recovery, recovery)
            if isinstance(error, asyncio.CancelledError) and error.__cause__ is None:
                raise
            raise DecisionUnavailable("ledger_unreliable") from error

    def _save_recovery(self, text: str) -> None:
        with self._recovery.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())


class ReviewAccounting:
    def __init__(
        self, live: LiveRun, target: ChangeTarget, profile: ModelProfile, agent_id: str
    ) -> None:
        self.live, self.target, self.profile, self.agent_id = live, target, profile, agent_id
        self.failure: ReviewLedgerFailure | None = None

    async def record(self, reply: ModelReply) -> None:
        # Shield the whole receipt, including event publication, from target invalidation.
        await durable(self._record(reply))

    async def _record(self, reply: ModelReply) -> None:
        live = self.live
        metered = self.profile.provider == "meta"
        price = live._price_for(self.profile)
        cost = price.cost(reply.usage) if price is not None else None
        spent = live._usage.record(cost) if metered else live._usage.spent_usd
        tick = UsageTick(
            agent_id=self.agent_id,
            change_id=self.target.change_id,
            role="review",
            model=reply.model,
            input_tokens=reply.usage.input_tokens,
            output_tokens=reply.usage.output_tokens,
            cached_tokens=reply.usage.cached_tokens,
            cost_usd=cost,
            spent_usd=spent,
            budget_usd=live._usage.budget_usd,
            brake=live._usage.brake if metered else "ok",
        )
        validate_usage_tick(tick)
        state = live.ledger.state
        if metered:
            try:
                state = await write_tick(live, tick)
            except (asyncio.CancelledError, LedgerError, OSError) as error:
                cause = error.__cause__ if isinstance(error, asyncio.CancelledError) else error
                if not isinstance(cause, LedgerError | OSError):
                    raise
                # No await before the brake and this review's failure are both recorded.
                live._ledger_lost = True
                live.ledger.notify_loss()
                self.failure = ReviewLedgerFailure(
                    f"The review ledger write failed ({type(cause).__name__})."
                )
                raise self.failure from cause
        if state is not None:
            tick = tick.model_copy(update={"spent_usd": state.spent_usd})
        # Do not route through _publish: metering belongs to the reviewer, not tick.agent_id.
        receipt = await live.store.publish(tick)
        if live._manager is not None:
            live._manager.usage_recorded(receipt)
            if state is not None:
                live._manager.spend_received(receipt)

    async def report_failure(self) -> None:
        assert self.failure is not None
        target, profile = self.target, self.profile
        reason = (
            f"Cross-review model={profile.model}; provider={profile.provider}; "
            f"base={target.base_commit}; main={target.main_commit}; tree={target.candidate_tree}. "
            f"{self.failure} "
            "Stop the server and settle with Meta's console total before the next run."
        )
        await self.live._finish(
            self.live._report(
                "server",
                format_needs_you(target.revision, target.head_commit, "ledger", reason),
                agent_id=self.agent_id,
                change_id=target.change_id,
            )
        )
