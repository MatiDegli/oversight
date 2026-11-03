"""Narrow composition helpers, independent of future HTTP/event contracts."""

from collections.abc import Awaitable, Callable
from dataclasses import replace

from oversight.workspace import PreviewCandidate, PreviewManager, PreviewProfile, Workspace


def compose(
    workspace: Workspace,
    profile: PreviewProfile,
    *,
    trusted_demo: bool,
    resolve_candidate: Callable[[str, str, int, str], Awaitable[PreviewCandidate]],
    memory_hold: Callable[[], str | None],
) -> PreviewManager:
    """Freeze the demo trust clamp without widening the startup opening flag."""
    frozen = replace(profile, trusted_demo=trusted_demo and profile.trusted_demo)
    return PreviewManager(
        workspace, frozen, resolve_candidate=resolve_candidate, memory_hold=memory_hold
    )


async def drain_shutdown(previews: PreviewManager) -> None:
    await previews.drain("shutdown")
