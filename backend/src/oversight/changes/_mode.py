"""Confirmed merge mode, its CAS revision and outstanding event receipt.

Lock order is main -> worktree/temporary -> mode -> Git lease. This path takes
only its own mode lock; availability is a startup fact, never a probe or Git call.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from oversight.changes._state import WrongState
from oversight_contracts.commands import SetMergeMode
from oversight_contracts.policy import MergeModeState

if TYPE_CHECKING:
    from oversight.changes._support import ChangeContext


@dataclass(frozen=True)
class StartCapability:
    """Confirmed startup capability, supplied by the future isolation adapter."""

    confirmed: bool
    reason: str


def closed_start_capability() -> StartCapability:
    return StartCapability(
        False, "Isolated start capability is unavailable; launcher not connected"
    )


class ModeController:
    def __init__(
        self,
        *,
        availability: Callable[[], StartCapability] = closed_start_capability,
        timeout_s: float = 5.0,
    ) -> None:
        self._state = MergeModeState()
        self._lock = asyncio.Lock()
        self._availability = availability
        self._timeout_s = timeout_s
        self._receipt: asyncio.Task[None] | None = None
        self._suspended = False

    def snapshot(self) -> MergeModeState:
        """An independent copy of the last confirmed mode and revision."""
        return self._state.model_copy(deep=True)

    def authority_suspended(self) -> bool:
        """A pending or failed receipt cannot authorize an automatic merge."""
        return self._suspended

    async def set(self, ctx: "ChangeContext", command: SetMergeMode) -> MergeModeState:
        """CAS under only the mode lock; lock and confirmation share a five-second bound.

        A timeout or caller cancellation leaves the tracked receipt running. No later
        write overtakes it. Failure keeps authority suspended; disabling can be retried.
        """
        try:
            async with asyncio.timeout(self._timeout_s):
                async with self._lock:
                    if self._receipt is not None and not self._receipt.done():
                        await asyncio.shield(self._receipt)
                    if command.revision != self._state.revision:
                        raise WrongState(
                            "Merge mode revision is stale; read the current mode again"
                        )
                    if command.mode == self._state.mode:
                        return self.snapshot()
                    if command.mode == "merge_when_safe":
                        if self.authority_suspended():
                            raise WrongState(
                                "Merge authority is suspended: event receipt unconfirmed"
                            )
                        capability = self._availability()
                        if not capability.confirmed:
                            raise WrongState(capability.reason)
                    next_state = MergeModeState(
                        mode=command.mode, revision=self._state.revision + 1
                    )
                    self._suspended = True
                    self._receipt = ctx.spawn(self._confirm(ctx, next_state))
                    self._receipt.add_done_callback(self._observe_receipt)
                    await asyncio.shield(self._receipt)
                    return self.snapshot()
        except TimeoutError as error:
            raise WrongState(
                "Merge mode lock or event confirmation timed out after 5 seconds"
            ) from error

    async def _confirm(self, ctx: "ChangeContext", state: MergeModeState) -> None:
        try:
            await ctx.publish(state.model_copy(deep=True))
        except Exception as error:
            raise WrongState(
                "Merge mode event was not confirmed; authority remains suspended"
            ) from error
        self._state = state
        self._suspended = False

    @staticmethod
    def _observe_receipt(receipt: asyncio.Task[None]) -> None:
        # Retrieve failures even if a timed-out/cancelled caller never comes back.
        if not receipt.cancelled():
            receipt.exception()
