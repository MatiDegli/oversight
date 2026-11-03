"""Private preview interface, deliberately independent of versioned HTTP/event contracts."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from oversight.workspace.errors import WorkspaceError

if TYPE_CHECKING:
    from oversight.workspace.core import Tree

type PreviewState = Literal["starting", "ready", "stopping", "stopped", "failed"]
type PreviewReason = Literal[
    "closed",
    "lease_expired",
    "lifetime_expired",
    "mutation",
    "shutdown",
    "app_exited",
    "readiness_timeout",
    "launch_failed",
    "isolation_unavailable",
    "memory_limit",
    "cleanup_unverified",
    "worktree_changed",
    "profile_disabled",
]

ISOLATION_MESSAGE = "Live preview unavailable: isolation C is unavailable for this session."


class PreviewError(WorkspaceError):
    """Only fixed, safe messages cross this boundary; never child diagnostics."""

    def __init__(self, message: str = "Live preview is unavailable.", *, code: str = "wrong_state"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PreviewCandidate:
    run_id: str
    agent_id: str
    change_id: str
    revision: int
    head_commit: str
    tree: "Tree" = field(repr=False, compare=False)
    current: Callable[[], bool] = field(default=lambda: True, repr=False, compare=False)
    eligible: Callable[[], bool] = field(default=lambda: True, repr=False, compare=False)


@dataclass(frozen=True)
class PreviewStatusRecord:
    preview_id: str
    run_id: str
    agent_id: str
    change_id: str
    revision: int
    head_commit: str
    state: PreviewState
    reason: PreviewReason | None = None

    def __post_init__(self) -> None:
        states = {"starting", "ready", "stopping", "stopped", "failed"}
        reasons = {
            "closed",
            "lease_expired",
            "lifetime_expired",
            "mutation",
            "shutdown",
            "app_exited",
            "readiness_timeout",
            "launch_failed",
            "isolation_unavailable",
            "memory_limit",
            "cleanup_unverified",
            "worktree_changed",
            "profile_disabled",
        }
        if (
            self.state not in states
            or (self.reason is not None and self.reason not in reasons)
            or (self.state in {"starting", "ready"}) != (self.reason is None)
        ):
            raise ValueError("invalid preview transition reason")


@dataclass(frozen=True)
class PreviewCreatedResult(PreviewStatusRecord):
    lease_token: str = field(default="", repr=False)


@dataclass(frozen=True)
class PreviewDetailsResult(PreviewStatusRecord):
    url: str | None = None


type PreviewStatusSink = Callable[[PreviewStatusRecord], Awaitable[None]]
type CandidateResolver = Callable[[str, str, int, str], Awaitable[PreviewCandidate]]


async def preserve_ownership[T](work: Awaitable[T]) -> T:
    """Do not lose a just-spawned transport/identity to cancellation at the spawn boundary."""
    pending = asyncio.ensure_future(work)
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                continue
        pending.result()
        raise


class FakeStatusSink:
    """Deterministic awaited sink for consumers testing before the future event contract."""

    def __init__(self) -> None:
        self.records: list[PreviewStatusRecord] = []

    async def __call__(self, record: PreviewStatusRecord) -> None:
        self.records.append(record)
