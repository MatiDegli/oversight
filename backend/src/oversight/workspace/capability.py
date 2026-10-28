"""Session isolation authority, independent of local acceptance and Git integrity latches."""

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from oversight.workspace.isolation import IsolatedRunner, IsolationUnavailable
from oversight.workspace.runner import Spawn
from oversight.workspace.snapshot import IntegrityState

_log = logging.getLogger(__name__)

type IsolationPolicy = Literal["required", "best_effort"]

OPEN_NOTICE = (
    "Network isolation C is unavailable for this session. Ask me tests run with network "
    "access, without an acceptance step. Rules, their probes and Revert remain disabled "
    "until restart. C removes the network; it does not isolate the filesystem."
)


@dataclass(frozen=True)
class IsolationCapability:
    checked: bool
    born_with_c: bool
    invalidated: bool
    rules_suspended: bool
    reason: str | None

    @property
    def available(self) -> bool:
        return self.born_with_c and not self.invalidated

    @property
    def rules_available(self) -> bool:
        return self.available and not self.rules_suspended


async def check_effect(runtime: Path) -> None:
    """Exercise C1's launch/confirmation and network effect, without a suite or demo venv."""
    accepted = asyncio.Event()

    async def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        accepted.set()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(connected, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        await writer.wait_closed()
        await asyncio.wait_for(accepted.wait(), 2)
        runner = IsolatedRunner(runtime, port=port)
        result = await runner([sys.executable, "-I", "-S", "-c", "pass"], cwd=runtime, timeout_s=5)
        if (
            result.returncode != 0
            or result.timed_out
            or not runner.cleanup_complete
            or not runner.entry
            or runner.entry.get("refused") is not True
        ):
            raise IsolationUnavailable("isolation effect probe failed")
    finally:
        server.close()
        await server.wait_closed()


class SessionIsolation:
    def __init__(self, runtime: Path, integrity: IntegrityState) -> None:
        self.runtime = runtime
        self.integrity = integrity
        self._checked = False
        self._born = False
        self._invalidated = False
        self._reason: str | None = "startup isolation check has not completed"
        self._lock = asyncio.Lock()

    @property
    def capability(self) -> IsolationCapability:
        return IsolationCapability(
            self._checked,
            self._born,
            self._invalidated,
            self.integrity.rules_suspended,
            self._reason or self.integrity.reason,
        )

    async def check(self) -> IsolationCapability:
        async with self._lock:
            if not self._checked:
                try:
                    await self.verify_effect()
                except (OSError, TimeoutError) as error:
                    self._reason = str(error)
                else:
                    self._born = True
                    self._reason = None
                self._checked = True
        return self.capability

    async def verify_effect(self) -> None:
        """A later failed effect check can revoke C, but can never grant it."""
        try:
            await check_effect(self.runtime)
        except (OSError, TimeoutError) as error:
            if self._born:
                self.invalidate(str(error))
            raise

    def invalidate(self, reason: str) -> None:
        if self._invalidated:
            return
        self._invalidated = True
        self._reason = reason.replace(str(self.runtime), ".runtime")
        _log.warning("Session isolation revoked: %s", self._reason)

    def select(self, policy: IsolationPolicy, injected: Spawn | None, open_runner: Spawn) -> Spawn:
        if policy not in ("required", "best_effort"):
            raise ValueError("unknown isolation policy")
        cap = self.capability
        if (policy == "required" and not cap.available) or (self._born and cap.invalidated):
            raise IsolationUnavailable("isolation unavailable: " + str(cap.reason))
        if cap.available:
            # Once born with C, even an injected open runner cannot bypass it.
            return (
                injected if isinstance(injected, IsolatedRunner) else IsolatedRunner(self.runtime)
            )
        return injected or open_runner
