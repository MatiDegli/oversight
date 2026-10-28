"""Durable writes shared by author and review; review failures never halt the author."""

# Runtime composition helpers access LiveRun's owned ledger and event store.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from oversight.agents import LedgerError, ModelReply, UnknownUsage, validate_usage_tick
from oversight.events import format_needs_you, format_ts
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
) -> None:
    entry = UnknownUsage(live.run_id, tick.agent_id, tick.model, format_ts(datetime.now(UTC)))
    await durable(live.ledger.record(tick.cost_usd, entry), wait=wait)


class ReviewLedgerFailure(Exception):
    """This review's paid attempt could not be recorded; no author state is implicated."""


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
        if metered:
            try:
                await write_tick(live, tick)
            except (asyncio.CancelledError, LedgerError, OSError) as error:
                cause = error.__cause__ if isinstance(error, asyncio.CancelledError) else error
                if not isinstance(cause, LedgerError | OSError):
                    raise
                # No await before the brake and this review's failure are both recorded.
                live._ledger_lost = True
                self.failure = ReviewLedgerFailure(
                    f"The review ledger write failed ({type(cause).__name__})."
                )
                raise self.failure from cause
        # Do not route through _publish: metering belongs to the reviewer, not tick.agent_id.
        receipt = await live.store.publish(tick)
        if live._manager is not None:
            live._manager.usage_recorded(receipt)

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
