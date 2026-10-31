"""The manager's person-only Revert admission and shutdown port for one live run."""

import asyncio

from oversight.changes._revert import Person, RevertRefused, RevertService, RevertTarget
from oversight.changes._state import WrongState
from oversight.changes._support import ChangeContext
from oversight_contracts.commands import RevertChange
from oversight_contracts.events import RevertResult


class RevertPort:
    def __init__(self, ctx: ChangeContext, run_id: str, test_timeout_s: float) -> None:
        self.service = RevertService(ctx=ctx, run_id=run_id, test_timeout_s=test_timeout_s)
        self._tasks: set[asyncio.Task[RevertResult]] = set()

    def revert(self, target: RevertTarget, command: RevertChange) -> None:
        isolation = self.service.ctx.workspace.isolation
        if not isolation.rules_available:
            raise WrongState(
                "Rules authority is suspended until restart"
                if isolation.available and isolation.rules_suspended
                else "Network isolation C is unavailable for this session"
            )
        try:
            task = self.service.submit(target, command, principal=Person)
        except RevertRefused as error:
            raise WrongState(str(error)) from error
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def aclose(self) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()
        await self.service.aclose()
