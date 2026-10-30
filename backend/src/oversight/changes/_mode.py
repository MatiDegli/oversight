"""Confirmed merge mode, its CAS revision and outstanding event receipt.

Lock order is main -> worktree/temporary -> mode -> Git lease. This path takes
only its own mode lock; availability reads session facts, never a probe or Git call.
"""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from oversight.changes._state import WrongState
from oversight.slots import resolve_profile, resolve_slots
from oversight_contracts.commands import SetMergeMode
from oversight_contracts.policy import MergeModeState

if TYPE_CHECKING:
    from oversight.changes._support import ChangeContext
    from oversight.settings import Settings
    from oversight.workspace import Workspace


# Only the activation front may open this latch. No environment or setting controls it.
RULES_ACTIVATION = False
_CLOSED_REASON = "Rules mode is closed until Revert, protected paths and follow-main land"


@dataclass(frozen=True)
class StartCapability:
    """Current session authority, read without launching or confirming isolation."""

    confirmed: bool
    reason: str


def closed_start_capability() -> StartCapability:
    return StartCapability(False, _CLOSED_REASON)


def start_capability(workspace: "Workspace", settings: "Settings") -> StartCapability:
    """Pure synchronous adapter; each read observes the workspace's monotonic latches."""
    isolation = workspace.isolation
    if not isolation.rules_available:
        reason = (
            "Rules authority is suspended until restart"
            if isolation.available and isolation.rules_suspended
            else "Network isolation C is unavailable for this session"
        )
        return StartCapability(False, reason)
    if workspace.integrity.main_suspect:
        return StartCapability(False, "Main's integrity is uncertain until restart")
    if not settings.cross_review:
        return StartCapability(False, "Cross-review is off")
    reviewer = resolve_profile(settings.review_model)
    if any(slot.profile.model == reviewer.model for slot in resolve_slots(settings).values()):
        return StartCapability(False, "The reviewer model must differ from each author model")
    if not RULES_ACTIVATION:
        return closed_start_capability()
    return StartCapability(True, "Rules prerequisites confirmed")


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
        self._loss_reported = False

    def snapshot(self) -> MergeModeState:
        """An independent copy of the last confirmed mode and revision."""
        return self._state.model_copy(deep=True)

    def authority_suspended(self) -> bool:
        """A pending or failed receipt cannot authorize an automatic merge."""
        return self._suspended

    def capability(self) -> StartCapability:
        """Read current session facts; this port must never launch a probe."""
        return self._availability()

    def report_loss(self, ctx: "ChangeContext", reason: str) -> None:
        """One session notice, independent of each wait's Needs you receipt."""
        if self._state.mode == "merge_when_safe" and not self._loss_reported:
            self._loss_reported = True
            ctx.spawn(ctx.report("server", f"Rules authority suspended: {reason}", fatal=False))

    @asynccontextmanager
    async def held(self) -> AsyncGenerator[None]:
        """Final authorization: caller owns main/worktree, but not the Git lease."""
        async with self._lock:
            yield

    async def set(self, ctx: "ChangeContext", command: SetMergeMode) -> MergeModeState:
        """CAS under only the mode lock; lock and confirmation share the configured timeout.

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
                        capability = self.capability()
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
                f"Merge mode lock or event confirmation timed out after {self._timeout_s:g} seconds"
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
        ctx.assessment.mode_changed()

    @staticmethod
    def _observe_receipt(receipt: asyncio.Task[None]) -> None:
        # Retrieve failures even if a timed-out/cancelled caller never comes back.
        if not receipt.cancelled():
            receipt.exception()
