"""Measure the prepared combination from immutable Git objects, against captured main."""

from oversight.changes._policy import PolicyInputs, assess
from oversight.changes._support import ChangeContext, RulesFailure
from oversight.changes._versions import parse
from oversight.graph import change_cone, change_graph, possible_callers
from oversight.workspace import MergeGuard
from oversight_contracts.events import Limits
from oversight_contracts.policy import ChangeAssessment, RuleAssessment


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


async def measure(
    ctx: ChangeContext, guard: MergeGuard, pending: ChangeAssessment, limits: Limits
) -> tuple[RuleAssessment, ...]:
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
            possible_callers=len(possible_callers(candidate.graph, cone)),
            parse_errors=len(base.graph.parse_errors) + len(candidate.graph.parse_errors),
            unresolved_removed=len(delta.nodes_removed),
            evidence={r.rule: tuple(r.evidence) for r in pending.rules},
        ),
        pending.policy_version,
    )
    return tuple(r for r in measured.rules if r.rule in {"small_change", "small_cone"})
