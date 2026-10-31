"""Read-only evidence for a bounded inverse; never authorization for a later Git effect."""

import asyncio
import math
import os
import re
import time
from dataclasses import dataclass
from typing import Literal

from oversight.changes._policy import merges_alone
from oversight.events import StoredEvent
from oversight.workspace import Workspace, WorkspaceError
from oversight_contracts.commands import RevertChange
from oversight_contracts.events import ChangeResolved, ChangeResolvedEvent
from oversight_contracts.policy import RuleAssessment

_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", re.ASCII)
_MARKERS = (
    "MERGE_HEAD",
    "MERGE_MSG",
    "AUTO_MERGE",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "rebase-apply",
    "rebase-merge",
    "sequencer",
    "BISECT_LOG",
    "index.lock",
    "HEAD.lock",
)


class _Refusal(ValueError):
    """A fixed internal diagnostic, safe to return without process output."""


@dataclass(frozen=True)
class RevertPreflight:
    status: Literal["clean", "conflict"]
    main_commit: str | None
    merge_commit: str | None
    diagnostic: str
    candidate_tree: str | None = None

    def __post_init__(self) -> None:
        if self.status == "clean":
            if not all(
                _valid_oid(oid)
                for oid in (self.main_commit, self.merge_commit, self.candidate_tree)
            ):
                raise _Refusal("clean requires captured commits and a validated tree")
        elif self.status != "conflict" or self.candidate_tree is not None:
            raise _Refusal("uncertainty cannot expose a candidate tree")


def _valid_oid(value: object) -> bool:
    return isinstance(value, str) and _OID.fullmatch(value) is not None


def _oid_line(output: str) -> str:
    if not output.endswith("\n") or not _valid_oid(output[:-1]):
        raise _Refusal("Git did not return one complete object ID")
    return output[:-1]


def _consistent_rule(rule: RuleAssessment) -> bool:
    """Cross-check recorded pass claims; grant-covered unknowns retain their observations."""
    if rule.result != "pass":
        return rule.result == "unknown"  # rules_satisfied separately verifies grant coverage
    data = rule.observed
    if data is None:
        return False

    def number(key: str) -> float:
        value = data.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise _Refusal("missing numeric rule observation")
        return value

    match rule.rule:
        case "combined_tests":
            return (
                data.get("outcome") == "passed"
                and number("passed") >= 1
                and number("failed") == number("skipped") == 0
            )
        case "small_cone":
            return number("nodes") <= 5 and all(
                number(key) == 0
                for key in (
                    "omitted_by_depth",
                    "omitted_by_limit",
                    "parse_errors",
                    "unresolved_removed",
                )
            )
        case "small_change":
            return (
                1 <= number("files") <= 3 and number("lines_added") + number("lines_removed") <= 60
            )
        case "known_spend":
            return (
                number("budget_usd") > 0
                and number("spent_usd") / number("budget_usd") < 0.5
                and number("unknown_costs") == 0
                and data.get("ledger_ok") is True
            )
        case "existing_tests_untouched":
            return data.get("modified_existing_paths") == data.get("deleted_existing_paths") == []
        case "cross_review" | "test_integrity" | "added_tests_fail_before" | "protected_paths":
            return number("violations") == 0 and data.get("complete") is True
        case _:
            return False  # an unsupported rule can never substantiate a pass


def _resolution(event: object, command: RevertChange, change_id: str) -> ChangeResolved:
    if isinstance(event, StoredEvent):
        parsed = ChangeResolvedEvent.model_validate_json(
            event.envelope, strict=True, context={"live": True}
        )
        if (event.seq, event.ts, event.type) != (parsed.seq, parsed.ts, parsed.type):
            raise _Refusal("stored event metadata differs from its envelope")
        event = parsed
    if not isinstance(event, ChangeResolvedEvent):
        raise _Refusal("expected a server resolution event")
    # Reparse a detached representation: constructed or mutated models are not validation.
    valid = ChangeResolvedEvent.model_validate(
        event.model_dump(mode="json"), strict=True, context={"live": True}
    )
    RevertChange.model_validate(command.model_dump(mode="json"), strict=True)
    r = valid.payload
    a = r.assessment
    if (
        valid.type != "change.resolved"
        or valid.seq != command.resolution_seq
        or r.change_id != change_id
        or r.merge_commit != command.merge_commit
        or r.decided_by != "rules"
        or r.decision != "approved"
        or r.merge != "merged"
        or r.reason is not None
        or a is None
        or r.head_commit != a.head_commit
        or not merges_alone(a, r.grants or ())
        or not all(_consistent_rule(rule) for rule in a.rules)
        or not all(
            _valid_oid(oid)
            for oid in (
                command.main_commit,
                command.merge_commit,
                a.main_commit,
                a.head_commit,
                a.base_commit,
                a.candidate_tree,
            )
        )
        or any(
            ref.event_seq is not None and ref.event_seq >= valid.seq
            for rule in a.rules
            for ref in rule.evidence
        )
    ):
        raise _Refusal("resolution is not a matching, verifiable approved rules merge")
    return r


class _Probe:
    def __init__(self, workspace: Workspace, deadline: float) -> None:
        self.workspace = workspace
        self.deadline = deadline

    async def git(self, *args: str) -> str:
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("preflight deadline exceeded")
        result = await self.workspace.git(self.workspace.main, *args, timeout_s=left)
        if result.timed_out or result.truncated or result.returncode != 0 or result.stderr:
            raise _Refusal(f"Git {args[0]} did not return complete successful evidence")
        return result.stdout

    async def commit(self, oid: str) -> str:
        actual = _oid_line(await self.git("rev-parse", "--verify", f"{oid}^{{commit}}"))
        if actual != oid:
            raise _Refusal("commit identity differs")
        return actual

    async def main(self) -> str:
        ws = self.workspace
        directory = ws.main.git_identity.directory
        markers = [directory / name for name in _MARKERS]
        markers.append(ws.main.git_identity.common / "refs/heads/main.lock")
        if await asyncio.to_thread(lambda: any(os.path.lexists(p) for p in markers)):
            raise _Refusal("main has an in-progress operation or lock")
        if await self.git("symbolic-ref", "-q", "HEAD") != "refs/heads/main\n":
            raise _Refusal("HEAD is not attached to main")
        head = _oid_line(await self.git("rev-parse", "--verify", "HEAD^{commit}"))
        branch = _oid_line(await self.git("rev-parse", "--verify", "refs/heads/main^{commit}"))
        if head != branch:
            raise _Refusal("HEAD and main disagree")
        if await self.git("status", "--porcelain=v1", "-z", "--untracked-files=all"):
            raise _Refusal("main is dirty")
        flags = await self.git("ls-files", "-v", "-z")
        if (flags and not flags.endswith("\0")) or any(
            not row.startswith("H ") or len(row) <= 2 for row in flags.split("\0") if row
        ):
            raise _Refusal("main index flags hide checkout evidence")
        return head

    async def inverse(self, r: ChangeResolved, head: str, merge: str) -> str:
        a = r.assessment
        assert a is not None  # validated before acquiring Git evidence
        await self.commit(merge)
        await self.commit(a.main_commit)
        await self.commit(a.head_commit)
        await self.commit(a.base_commit)
        if await self.git("merge-base", "--is-ancestor", merge, head):
            raise _Refusal("unexpected ancestry output")
        metadata = await self.git("show", "-s", "--format=%P%n%T%n%an <%ae>%n%cn <%ce>%n%B", merge)
        expected = (
            f"{a.main_commit} {a.head_commit}\n{a.candidate_tree}\n"
            "rules <rules@oversight.invalid>\nrules <rules@oversight.invalid>\n"
            f"Merge {r.change_id} ({r.agent_id})\n\nMerged-by: rules\n\n"
        )
        if metadata != expected:
            raise _Refusal("merge structure, tree or rules provenance differs")
        candidate = _oid_line(
            await self.git(
                "merge-tree", "--write-tree", f"--merge-base={merge}", head, a.main_commit
            )
        )
        if await self.git("cat-file", "-t", candidate) != "tree\n":
            raise _Refusal("candidate is not a tree object")
        # Validate connectivity and object integrity without repairing or writing metadata.
        if await self.git("fsck", "--strict", "--no-dangling", "--no-reflogs", candidate):
            raise _Refusal("unexpected object verification output")
        return candidate


async def preflight(
    workspace: Workspace,
    event: object,
    command: RevertChange,
    change_id: str,
    *,
    timeout_s: float = 30,
) -> RevertPreflight:
    """Classify against captured main under both locks; unknown always needs the person.

    Only unreachable objects may be written. No checkout, index, ref or worktree changes.
    Cancellation propagates after the guarded runner's cleanup, while locks remain held.
    """
    head = merge = None
    try:
        r = _resolution(event, command, change_id)
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise _Refusal("preflight requires a finite positive deadline")
        deadline = time.monotonic() + timeout_s
        async with asyncio.timeout(timeout_s), workspace.locks(main=True), workspace.git_lease():
            probe = _Probe(workspace, deadline)
            head = await probe.main()
            if head != command.main_commit:
                raise _Refusal("observed main is stale")
            merge = await probe.commit(command.merge_commit)
            candidate = await probe.inverse(r, head, merge)
            if await probe.main() != head:
                raise _Refusal("main changed during classification")
            if time.monotonic() >= deadline:
                raise _Refusal("preflight deadline exceeded")
            return RevertPreflight(
                "clean", head, merge, "Inverse applies to captured main.", candidate
            )
    except (WorkspaceError, ValueError, OSError, TimeoutError, ArithmeticError) as error:
        # Fixed diagnostics never reflect provider data, paths or arbitrary process output.
        return RevertPreflight(
            "conflict",
            head,
            merge,
            str(error)
            if isinstance(error, _Refusal)
            else f"Preflight unavailable: {type(error).__name__}.",
        )
