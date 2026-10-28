"""Shared, closed terminal-reason vocabulary for server producers and replay consumers."""

import asyncio
import re
from dataclasses import dataclass

from oversight_contracts.events import ErrorReport

NEEDS_YOU_CODES = (
    "main_suspect",
    "main_moved",
    "target_changed",
    "worktree_unverifiable",
    "unproposed_edits",
    "policy_invalid",
    "spend_invalid",
    "isolation_unavailable",
    "integrity_invalid",
    "cancelled",
    "git_failed",
    "suite_failed",
    "delivery_failed",
    "reviewer_missing",
    "reviewer_same",
    "reviewer_credentials",
    "author_halted",
    "review_timeout",
    "review_invalid",
    "review_truncated",
    "review_empty",
    "review_api_failed",
    "round_exhausted",
    "ledger",
)
NEEDS_YOU_TEMPLATE = "Needs you — revision={revision}; head={head}; code={code}; reason={reason}"

_PATTERN = re.compile(
    r"Needs you — revision=(0|[1-9][0-9]*); head=([^;\s]+); code=([a-z_]+); reason=([\s\S]+)"
)


@dataclass(frozen=True)
class NeedsYou:
    revision: int
    head: str
    code: str
    reason: str


def format_needs_you(revision: int, head: str, code: str, reason: str) -> str:
    """Render one target; reason is opaque, including delimiters and line breaks."""
    message = NEEDS_YOU_TEMPLATE.format(revision=revision, head=head, code=code, reason=reason)
    if parse_needs_you(message) != NeedsYou(revision, head, code, reason):
        raise ValueError("invalid Needs you target, code or reason")
    return message


def parse_needs_you(message: str) -> NeedsYou | None:
    """Recognize only the complete ordered template and a known code, never reason contents."""
    match = _PATTERN.fullmatch(message)
    if match is None or match[3] not in NEEDS_YOU_CODES or not match[4].strip():
        return None
    return NeedsYou(int(match[1]), match[2], match[3], match[4])


def needs_you_golden() -> dict[str, object]:
    """Export the vocabulary for the shared golden; consumers never maintain another list."""
    return {"template": NEEDS_YOU_TEMPLATE, "codes": list(NEEDS_YOU_CODES)}


def terminal_key(report: ErrorReport) -> tuple[str, int, str, str] | None:
    """Only a normalized error naming a change is a terminal signal."""
    note = parse_needs_you(report.message)
    if report.change_id is None or note is None:
        return None
    return report.change_id, note.revision, note.head, note.code


@dataclass(frozen=True)
class NeedsYouWait:
    """A rules wait's captured target and generation, independent of historical diagnostics."""

    change_id: str
    revision: int
    head: str
    generation: int

    def accepts(self, report: ErrorReport, *, generation: int) -> bool:
        key = terminal_key(report)
        return (
            generation == self.generation
            and key is not None
            and key[:3] == (self.change_id, self.revision, self.head)
        )


@dataclass(frozen=True)
class TerminalClaim[T]:
    target: tuple[str, int, str]
    receipt: asyncio.Future[T | None]


class TerminalClaims[T]:
    """Synchronous claim/ack; released producers wake contenders to claim again."""

    def __init__(self) -> None:
        self._targets: dict[tuple[str, int, str], TerminalClaim[T]] = {}

    def claim(self, report: ErrorReport) -> tuple[TerminalClaim[T], bool]:
        key = terminal_key(report)
        if key is None:
            raise ValueError("only normalized Needs you errors can claim a target")
        target = key[:3]
        if target in self._targets:
            return self._targets[target], False
        claim = TerminalClaim(target, asyncio.get_running_loop().create_future())
        self._targets[target] = claim
        return claim, True

    def acknowledge(self, claim: TerminalClaim[T], receipt: T) -> None:
        if self._targets.get(claim.target) is claim and not claim.receipt.done():
            claim.receipt.set_result(receipt)

    def release(self, claim: TerminalClaim[T]) -> None:
        if self._targets.get(claim.target) is claim and not claim.receipt.done():
            del self._targets[claim.target]
            claim.receipt.set_result(None)
