"""Shared, closed terminal-reason vocabulary for server producers and replay consumers."""

import re
from dataclasses import dataclass

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
    message = f"Needs you — revision={revision}; head={head}; code={code}; reason={reason}"
    if parse_needs_you(message) != NeedsYou(revision, head, code, reason):
        raise ValueError("invalid Needs you target, code or reason")
    return message


def parse_needs_you(message: str) -> NeedsYou | None:
    """Recognize only the complete ordered template and a known code, never reason contents."""
    match = _PATTERN.fullmatch(message)
    if match is None or match[3] not in NEEDS_YOU_CODES or not match[4].strip():
        return None
    return NeedsYou(int(match[1]), match[2], match[3], match[4])
