"""Publish model usage and notify a sink from the committed receipt."""

# Runtime helpers access the agent run's owned accounting dependencies.
# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from oversight.events import StoredEvent
from oversight_contracts.events import UsageTick

if TYPE_CHECKING:
    from oversight.agents.client import ModelReply
    from oversight.agents.loop import AgentRun


@runtime_checkable
class UsageSink(Protocol):
    def usage_recorded(self, receipt: StoredEvent) -> None: ...


async def publish_usage(run: AgentRun, reply: ModelReply) -> None:
    cost = run._pricing.cost(reply.usage) if run._pricing is not None else None
    # Baseten receipts show cost without debiting Meta credits or inheriting its brake.
    meta_credits = run._model.profile.provider != "baseten"
    spent = run._ledger.record(cost) if meta_credits else run._ledger.spent_usd
    receipt = await run._publish(
        UsageTick(
            agent_id=run._task.agent_id,
            role="agent",
            change_id=run._task.change_id,
            model=reply.model,
            input_tokens=reply.usage.input_tokens,
            output_tokens=reply.usage.output_tokens,
            cached_tokens=reply.usage.cached_tokens,
            cost_usd=cost,
            spent_usd=spent,
            budget_usd=run._ledger.budget_usd,
            brake=run._ledger.brake if meta_credits else "ok",
        )
    )
    if isinstance(receipt, StoredEvent) and isinstance(run._sink, UsageSink):
        run._sink.usage_recorded(receipt)
