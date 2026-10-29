"""F2's runtime entry point for intentional difference decisions, with durable Baseten spend.

No evidence producer or automatic call site lives here. F2 supplies a captured DecisionInput,
awaits classify_difference, checks that target/revision/node is still current, then applies the
advisory semantics documented in oversight.agents.decision. Cancellation invalidates the result,
never the receipt. Calls are serialized to prevent two admissions against one stale balance.
"""

# Runtime composition accesses LiveRun's settings and owned registries.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from oversight.agents import (
    DecisionInput,
    DecisionMissing,
    DecisionOutcome,
    DecisionUnavailable,
    ModelReply,
    classify_difference,
)
from oversight.decision_config import configured_decision
from oversight.runtime_accounting import BasetenDecisionAccounting, durable

if TYPE_CHECKING:
    from oversight.app import LiveRun


class RuntimeDecisions:
    """LiveRun.difference_decisions; independent from its human decision/revision registry."""

    def __init__(self, live: LiveRun) -> None:
        self.live = live
        self.accounting = BasetenDecisionAccounting(live)
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[object]] = set()
        self._closed = False

    async def classify_difference(
        self, input: DecisionInput, *, deadline: float
    ) -> DecisionOutcome:
        """Include waiting for other candidates in the caller's total monotonic deadline."""
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        try:
            async with asyncio.timeout_at(deadline):
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError
                async with self._lock:
                    if self._closed:
                        raise DecisionUnavailable("closed")
                    await self.accounting.load()
                    await self.accounting.admit()
                    client = configured_decision(self.live._settings)

                    async def record(reply: ModelReply) -> None:
                        await self.accounting.record(input, reply)

                    try:
                        return await classify_difference(
                            client,
                            input,
                            deadline=deadline,
                            admission=self.accounting.admit,
                            accounting=record,
                        )
                    finally:
                        await durable(client.aclose())
        except DecisionUnavailable as error:
            return DecisionMissing(input.target, input.difference.node_id, error.code)
        except TimeoutError:
            return DecisionMissing(input.target, input.difference.node_id, "deadline")
        finally:
            self._tasks.discard(task)

    async def close(self) -> None:
        """Shutdown drains every sent attempt before the event store closes."""
        self._closed = True
        tasks = [task for task in self._tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await durable(asyncio.gather(*tasks, return_exceptions=True))
