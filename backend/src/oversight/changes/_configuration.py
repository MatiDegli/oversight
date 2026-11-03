"""Advisory whole-file attribution from immutable Git objects, separate from integrity."""

import asyncio
import json
import re
from pathlib import PurePosixPath

from oversight.changes._shortcuts_unknown import COLLECTION_CONFIG
from oversight.changes._state import ChangeRecord
from oversight.changes._support import ChangeContext
from oversight.workspace import Tree, validate_suite_execution
from oversight_contracts.review import ChangeTarget

PREFIX = "configuration attribution: "
LIMITATIONS = (
    "; analysis limitation: whole-file matching, pytest relevance unproven; "
    "ignored files not compared; configuration outside the repository unknown"
)
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_RAW = re.compile(r":([0-7]{6}) ([0-7]{6}) ([0-9a-f]+) ([0-9a-f]+) ([ADMT])\Z")


class _Unavailable(ValueError):
    """A fixed, bounded reason, never Git stderr or repository contents."""


def is_configuration(path: str) -> bool:
    """Exact-case inventory at any depth; no claim of pytest influence."""
    return PurePosixPath(path).name in COLLECTION_CONFIG | {"conftest.py"}


def unknown(reason: str) -> str:
    return PREFIX + f"configuration attribution unknown ({reason[:120]})" + LIMITATIONS


def _note(base: str, paths: tuple[str, ...], limit: int) -> str:
    if not paths:
        return PREFIX + f"no compared configuration differences (base {base})" + LIMITATIONS
    labels = [json.dumps(path, ensure_ascii=True) for path in paths[:limit]]
    shortened = any(len(label) > 200 for label in labels)
    display = ", ".join(label if len(label) <= 200 else label[:197] + "..." for label in labels)
    if len(paths) > limit:
        display += f"; {len(paths) - limit} paths omitted from display"
    if shortened:
        display += "; path labels shortened for display"
    return (
        PREFIX + f"test configuration changed by this change (base {base}): {display}; "
        "suite results reflect it" + LIMITATIONS
    )


async def _git(ctx: ChangeContext, tree: Tree, *args: str) -> str:
    result = await ctx.run(tree, *args)
    if result.timed_out:
        raise _Unavailable("comparison timed out")
    if result.truncated:
        raise _Unavailable("comparison output truncated; comparison incomplete")
    if result.returncode != 0:
        raise _Unavailable("Git objects unavailable")
    return result.stdout


def _paths(output: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Validate the entire NUL-delimited raw diff before trusting even an empty result."""
    if not output:
        return (), ()
    parts = output.split("\0")
    if parts.pop() != "" or len(parts) % 2:
        raise _Unavailable("malformed comparison output; comparison incomplete")
    paths: set[str] = set()
    blobs: set[str] = set()
    seen: set[str] = set()
    for header, path in zip(parts[::2], parts[1::2], strict=True):
        match = _RAW.fullmatch(header)
        relative = PurePosixPath(path)
        if (
            match is None
            or not path
            or relative.is_absolute()
            or ".." in relative.parts
            or str(relative) != path
            or "\ufffd" in path
            or path in seen
        ):
            raise _Unavailable("malformed comparison output; comparison incomplete")
        seen.add(path)
        old_mode, new_mode, old, new, status = match.groups()
        if (
            not _SHA.fullmatch(old)
            or not _SHA.fullmatch(new)
            or len(old) != len(new)
            or (old_mode == "000000") != (status == "A")
            or (new_mode == "000000") != (status == "D")
            or (set(old) == {"0"}) != (status == "A")
            or (set(new) == {"0"}) != (status == "D")
        ):
            raise _Unavailable("malformed comparison identities; comparison incomplete")
        if is_configuration(path):
            if old_mode not in {"000000", "100644", "100755"} or new_mode not in {
                "000000",
                "100644",
                "100755",
            }:
                raise _Unavailable("configuration is not a regular file")
            paths.add(path)
            blobs.update(sha for sha in (old, new) if set(sha) != {"0"})
    return tuple(sorted(paths)), tuple(sorted(blobs))


async def attribution(
    ctx: ChangeContext, tree: Tree, base: str | None, tested_tree: str | None, *, limit: int
) -> str:
    """Caller holds the agent lock; preserve cancellation and contain comparison failures."""
    try:
        if base is None or tested_tree is None:
            raise _Unavailable("missing Git identity")
        if not _SHA.fullmatch(base) or not _SHA.fullmatch(tested_tree):
            raise _Unavailable("invalid Git identity")
        async with asyncio.timeout(ctx.timeout):
            base_tree = await _git(ctx, tree, "rev-parse", "--verify", f"{base}^{{tree}}")
            captured = await _git(ctx, tree, "rev-parse", "--verify", f"{tested_tree}^{{tree}}")
            if not _SHA.fullmatch(base_tree.removesuffix("\n")) or captured != tested_tree + "\n":
                raise _Unavailable("malformed tree identity")
            output = await _git(
                ctx,
                tree,
                "diff",
                "--raw",
                "-z",
                "--no-abbrev",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                "--ignore-submodules=none",
                base_tree.removesuffix("\n"),
                tested_tree,
                "--",
            )
            paths, blobs = _paths(output)
            for blob in blobs:
                await _git(ctx, tree, "cat-file", "-e", f"{blob}^{{blob}}")
        return _note(base, paths, limit)
    except _Unavailable as error:
        return unknown(str(error))
    except TimeoutError:
        return unknown("comparison timed out")
    except Exception as error:
        return unknown(f"comparison failed ({type(error).__name__})")


async def proposal_attribution(ctx: ChangeContext, rec: ChangeRecord, target: ChangeTarget) -> str:
    """Use this revision's associated proposal capture; freeze the returned text in facts."""
    try:
        current = [
            (seq, execution)
            for seq, execution in rec.executions.items()
            if execution.phase == "proposal" and execution.revision == target.revision
        ]
        if len(current) != 1:
            return unknown("current proposal capture unavailable or ambiguous")
        seq, execution = current[0]
        run = rec.suite_runs[seq]
        validate_suite_execution(
            execution,
            run,
            change_id=rec.change_id,
            agent_id=rec.agent_id,
            revision=target.revision,
            phase="proposal",
        )
        if (
            execution.tested_commit != target.head_commit
            or execution.tested_tree != target.candidate_tree
            or target.base_commit != rec.base_commit
        ):
            return unknown("proposal capture does not match current candidate")
        return await attribution(ctx, rec.tree, rec.base_commit, run.tested_tree, limit=20)
    except Exception as error:
        return unknown(f"proposal comparison failed ({type(error).__name__})")
