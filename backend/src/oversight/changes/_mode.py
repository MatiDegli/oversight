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
    try:
        reviewer = resolve_profile(settings.review_model)
    except ValueError:
        return StartCapability(False, "The reviewer model is not configured")
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
        self._receipt: asyncio.Task[MergeModeState] | None = None
        self._suspended = False
        self._disables: set[object] = set()
        self._loss_reported = False

    def snapshot(self) -> MergeModeState:
        """An independent copy of the last confirmed mode and revision."""
        return self._state.model_copy(deep=True)

    def authority_suspended(self) -> bool:
        """Pending disables and pending or failed receipts suspend automatic authority."""
        return self._suspended or bool(self._disables)

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
        """Suspend disables before waiting; a caller timeout leaves its attempt running."""
        token = object() if command.mode == "ask_me" else None
        if token is not None:
            self._disables.add(token)
            ctx.assessment.mode_changed()
        attempt = ctx.spawn(self._attempt(ctx, command, token))
        attempt.add_done_callback(lambda task: self._attempt_done(task, token))
        try:
            async with asyncio.timeout(self._timeout_s):
                return await asyncio.shield(attempt)
        except (TimeoutError, asyncio.CancelledError) as error:
            # Enables retain their existing lock-wait cancellation behavior. Once a
            # receipt has started it must settle, just like every disable attempt.
            if token is None and self._receipt is not attempt:
                attempt.cancel()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise WrongState(
                f"Merge mode lock or event confirmation timed out after {self._timeout_s:g} seconds"
            ) from error

    async def _attempt(
        self, ctx: "ChangeContext", command: SetMergeMode, token: object | None
    ) -> MergeModeState:
        try:
            async with self._lock:
                if command.revision != self._state.revision:
                    raise WrongState("Merge mode revision is stale; read the current mode again")
                if command.mode == self._state.mode:
                    return self.snapshot()
                if command.mode == "merge_when_safe":
                    if self.authority_suspended():
                        raise WrongState("Merge authority is suspended: event receipt unconfirmed")
                    capability = self.capability()
                    if not capability.confirmed:
                        raise WrongState(capability.reason)
                next_state = MergeModeState(mode=command.mode, revision=self._state.revision + 1)
                self._suspended = True
                self._receipt = asyncio.current_task()
                await self._confirm(ctx, next_state)
                return self.snapshot()
        finally:
            if token is not None:
                self._disables.remove(token)

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

    def _attempt_done(self, attempt: asyncio.Task[MergeModeState], token: object | None) -> None:
        # Retrieve failures even if a timed-out/cancelled caller never comes back.
        if not attempt.cancelled():
            attempt.exception()
        elif token is not None:
            # Shutdown can cancel before the coroutine enters its try/finally.
            self._disables.discard(token)
