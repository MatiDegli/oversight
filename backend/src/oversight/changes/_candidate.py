"""Measure the prepared combination from immutable Git objects, against captured main."""

from dataclasses import dataclass

from oversight.changes._grants import Measurement
from oversight.changes._grants import measure as measure_grants
from oversight.changes._policy import PolicyInputs, assess
from oversight.changes._protected_paths import (
    ChangedFileInventory,
    ProtectedPathSnapshot,
    _valid_path,  # pyright: ignore[reportPrivateUsage]
)
from oversight.changes._support import ChangeContext, RulesFailure
from oversight.changes._versions import parse
from oversight.graph import change_cone, change_graph
from oversight.workspace import IntegrityError, LockOrderError, MergeGuard, WorkspaceError
from oversight_contracts.common import FileStatus
from oversight_contracts.events import FileStat, Limits
from oversight_contracts.policy import ChangeAssessment, EvidenceRef, RuleAssessment


async def changed_inventory(
    ctx: ChangeContext, guard: MergeGuard, proposal_seq: int
) -> ChangedFileInventory:
    """Capture every candidate path and status from immutable objects, never proposal statuses.

    Name-status does not measure line counts: those FileStat fields are zero here. A malformed
    or unavailable query discards all entries and retains its receipts as an incomplete source.
    """
    evidence = (
        EvidenceRef(event_seq=proposal_seq),
        EvidenceRef(git_object=guard.pre),
        EvidenceRef(git_object=guard.candidate_tree),
    )
    unavailable = ChangedFileInventory(files=(), complete=False, evidence=evidence)
    try:
        output = await ctx.git(
            ctx.workspace.main,
            "diff",
            "--name-status",
            "--no-renames",
            "-z",
            "--ignore-submodules=none",
            guard.pre,
            guard.candidate_tree,
        )
    except (IntegrityError, LockOrderError):
        raise
    except WorkspaceError:
        return unavailable
    if output and not output.endswith("\0"):
        return unavailable
    fields = output[:-1].split("\0") if output else []
    if len(fields) % 2:
        return unavailable
    statuses: dict[str, FileStatus] = {"A": "added", "M": "modified", "D": "deleted"}
    files: list[FileStat] = []
    for status, path in zip(fields[::2], fields[1::2], strict=True):
        if status not in statuses or not _valid_path(path) or "\ufffd" in path:
            return unavailable
        files.append(FileStat(path=path, status=statuses[status], lines_added=0, lines_removed=0))
    return ChangedFileInventory(files=tuple(files), complete=True, evidence=evidence)


async def python_files(ctx: ChangeContext, tree: str) -> dict[str, str]:
    main = ctx.workspace.main
    listing = await ctx.git(main, "ls-tree", "-r", "-z", tree)
    if listing and not listing.endswith("\0"):
        raise RulesFailure("git_failed", "Incomplete candidate file listing.")
    files: dict[str, str] = {}
    for entry in filter(None, listing.split("\0")):
        metadata, separator, path = entry.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise RulesFailure("git_failed", "Invalid candidate file listing.")
        mode, kind, oid = fields
        if mode == "160000" or any(p.casefold() == ".gitmodules" for p in path.split("/")):
            raise RulesFailure("integrity_invalid", "Candidate contains Git repository entries.")
        if kind == "blob" and mode in {"100644", "100755"} and path.endswith(".py"):
            files[path] = await ctx.git(main, "cat-file", "blob", oid)
    return files


@dataclass(frozen=True)
class Measured:
    rules: tuple[RuleAssessment, ...]
    grants: Measurement


async def measure(
    ctx: ChangeContext,
    guard: MergeGuard,
    pending: ChangeAssessment,
    limits: Limits,
    *,
    protected_paths: ProtectedPathSnapshot | None,
    proposal_seq: int,
) -> Measured:
    inventory = await changed_inventory(ctx, guard, proposal_seq)
    stats = await ctx.git(
        ctx.workspace.main,
        "diff",
        "--numstat",
        "--no-renames",
        "-z",
        guard.pre,
        guard.candidate_tree,
    )
    if stats and not stats.endswith("\0"):
        raise RulesFailure("git_failed", "Incomplete candidate size query.")
    paths: list[str] = []
    added = removed = 0
    for entry in filter(None, stats.split("\0")):
        plus, minus, path = entry.split("\t", 2)
        if not plus.isdecimal() or not minus.isdecimal():
            raise RulesFailure("policy_invalid", "Candidate line counts are unverifiable.")
        paths.append(path)
        added, removed = added + int(plus), removed + int(minus)
    base = await parse(await python_files(ctx, guard.pre), guard.pre, None)
    candidate = await parse(
        await python_files(ctx, guard.candidate_tree), guard.candidate_tree, None
    )
    delta = change_graph(base, candidate)
    cone = change_cone(
        delta, candidate.graph, max_depth=limits.cone_max_depth, max_nodes=limits.cone_max_nodes
    )
    grant_measurement = measure_grants(base.graph, candidate.graph, delta, paths, cone)
    measured = assess(
        PolicyInputs(
            change_id=pending.change_id,
            agent_id=pending.agent_id,
            revision=pending.revision,
            base_commit=pending.base_commit,
            head_commit=pending.head_commit,
            main_commit=guard.pre,
            candidate_tree=guard.candidate_tree,
            mode=pending.mode,
            mode_revision=pending.mode_revision,
            files=tuple(paths),
            lines_added=added,
            lines_removed=removed,
            cone=cone,
            possible_callers=grant_measurement.caller_count,
            parse_errors=len(base.graph.parse_errors) + len(candidate.graph.parse_errors),
            unresolved_removed=len(delta.nodes_removed),
            evidence={r.rule: tuple(r.evidence) for r in pending.rules},
            protected_paths=protected_paths,
            changed_files=inventory,
        ),
        pending.policy_version,
    )
    return Measured(
        tuple(
            r for r in measured.rules if r.rule in {"small_change", "small_cone", "protected_paths"}
        ),
        grant_measurement,
    )
