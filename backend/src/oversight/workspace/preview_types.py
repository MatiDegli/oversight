"""Private preview interface, deliberately independent of versioned HTTP/event contracts."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from oversight.workspace.errors import WorkspaceError
from oversight.workspace.integrity import IntegrityError

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


class PreviewRestoredError(IntegrityError):
    """Verified restitution refuses this operation without claiming cleanup uncertainty."""

    def __init__(self) -> None:
        super().__init__("Live preview restored Git metadata; operation refused.", ".git")


def valid_preview_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"preview-[0-9a-f]{32}", value) is not None


def valid_lease_token(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is not None


def validate_preview_id(value: object) -> None:
    if not valid_preview_id(value):
        raise PreviewError("Live preview request is invalid.", code="invalid_request")


class PreviewError(WorkspaceError):
    """Only fixed, safe messages cross this boundary; never child diagnostics."""

    def __init__(
        self,
        message: str = "Live preview is unavailable.",
        *,
        code: Literal[
            "wrong_state", "not_found", "forbidden", "invalid_request", "not_configured"
        ] = "wrong_state",
    ):
        super().__init__(message)
        self.code = code
        self.missing: list[object] | None = [] if code == "not_configured" else None


def validate_start(run_id: object, agent_id: object, revision: object, head_commit: object) -> None:
    if (
        not isinstance(run_id, str)
        or re.fullmatch(r"run-\d{8}-\d{6}", run_id) is None
        or not isinstance(agent_id, str)
        or re.fullmatch(r"agent-[1-9]\d*", agent_id) is None
        or type(revision) is not int
        or revision < 1
        or not isinstance(head_commit, str)
        or not head_commit
    ):
        raise PreviewError("Live preview request is invalid.", code="invalid_request")


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
            not valid_preview_id(self.preview_id)
            or self.state not in states
            or (self.reason is not None and self.reason not in reasons)
            or (self.state in {"starting", "ready"}) != (self.reason is None)
        ):
            raise ValueError("invalid preview transition reason")


@dataclass(frozen=True)
class PreviewCreatedResult(PreviewStatusRecord):
    lease_token: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not valid_lease_token(self.lease_token):
            raise ValueError("invalid preview lease token")


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
        self.changed = asyncio.Condition()

    async def __call__(self, record: PreviewStatusRecord) -> None:
        async with self.changed:
            self.records.append(record)
            self.changed.notify_all()

    async def wait(self, state: PreviewState, preview_id: str | None = None) -> PreviewStatusRecord:
        async with asyncio.timeout(5), self.changed:
            await self.changed.wait_for(
                lambda: any(
                    r.state == state and (preview_id is None or r.preview_id == preview_id)
                    for r in self.records
                )
            )
            return next(
                r
                for r in reversed(self.records)
                if r.state == state and (preview_id is None or r.preview_id == preview_id)
            )
