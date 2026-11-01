import asyncio
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace

from oversight.agents import AgentTask, RunEnd, task_examples
from oversight.changes import _delivery as delivery
from oversight.changes._behavior import probe_behavior, unavailable_behavior
from oversight.changes._behavior_publish import capture_behavior
from oversight.changes._diff import count_lines_async, file_status, parse_name_status
from oversight.changes._example_criteria import unverified_run, verify_run
from oversight.changes._facts import FactsEvidence, build_change_facts, capture_facts
from oversight.changes._measured import measure_cone, unavailable_measured
from oversight.changes._probes import added_cases, probe_added_tests
from oversight.changes._review_port import ReviewCapture
from oversight.changes._run_findings import EditReceipt, receipt
from oversight.changes._state import (
    SUSPECT,
    By,
    ChangeNotFound,
    ChangeRecord,
    Source,
    State,
    WrongState,
)
from oversight.changes._suite_evidence import publish_suite
from oversight.changes._support import ChangeContext
from oversight.changes._versions import (
    nodes_in_path,
    parse,
)
from oversight.graph import (
    CodeVersion,
    change_cone,
    change_graph,
)
from oversight.workspace import (
    SuiteInterrupted,
    SuiteUnavailable,
    Tree,
    WorkspaceError,
)
from oversight_contracts.common import AgentId, FileStatus
from oversight_contracts.events import (
    ChangeProposed,
    ChangeUpdated,
    FileDiff,
    FileStat,
    SuiteResult,
)


async def open(
    ctx: ChangeContext,
    task: AgentTask,
    model_id: str,
    *,
    before_create: Callable[[], None] | None = None,
) -> Tree:
    """Create the agent's worktree from main's HEAD and record its base files and graph."""
    if task.change_id in ctx.state.changes or task.agent_id in ctx.state.agents:
        raise WrongState(f"{task.agent_id} or {task.change_id} was already opened")
    await delivery.finish_pending(ctx)
    async with ctx.workspace.locks(main=True):
        # M5: the route's check is a fast path; main may have turned suspect while this queued.
        if ctx.state.suspect:
            raise WrongState(f"{SUSPECT}; nothing was started")
        # Answering admission: re-check after pending delivery and the main-lock wait.
        if before_create is not None:
            before_create()
        tree = await ctx.workspace.create_worktree(task.agent_id)
    base_commit = tree.base_commit
    if base_commit is None:
        raise WorkspaceError(f"{task.agent_id}'s worktree has no base commit")
    async with ctx.workspace.locks(agent_id=task.agent_id):
        files, base = ctx.state.bases.get(base_commit) or await read_base(ctx, tree, base_commit)
    rec = ChangeRecord(
        task,
        model_id,
        tree,
        base_commit,
        files,
        base,
        dict(files),
        base,
        change_graph(base, base),
    )
    ctx.state.changes[task.change_id] = rec
    ctx.state.agents[task.agent_id] = rec
    return tree


async def read_files(ctx: ChangeContext, tree: Tree) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in await tree.list_files():
        try:
            files[path] = (await tree.read_text(path)).content
        except WorkspaceError:
            continue  # oversized or not UTF-8 text: no agent tool can edit it
    return files


async def read_base(
    ctx: ChangeContext, tree: Tree, commit: str
) -> tuple[dict[str, str], CodeVersion]:
    files = await read_files(ctx, tree)
    found = (files, await parse(files, commit, None))
    ctx.state.bases[commit] = found
    return found


async def file_edited(ctx: ChangeContext, agent_id: AgentId, path: str) -> bool:
    """Send `change.updated` for an edit on disk; `False` after sending an `error` instead."""
    rec = ctx.state.agents.get(agent_id)
    try:
        if rec is None or rec.state != "working":
            raise WrongState(f"{agent_id} has no change in progress")
        rec.invalidate_cards()
        async with ctx.workspace.locks(agent_id=agent_id):
            for earlier in sorted(rec.unpublished - {path}):
                try:
                    await update(ctx, rec, earlier)
                except Exception as error:
                    await ctx.error("server", f"{earlier}: {error}", rec, fatal=False)
            await update(ctx, rec, path)
        return True
    except Exception as error:
        if rec is not None:
            rec.unpublished.add(path)
        await ctx.error("server", f"The edit of {path} was not shown: {error}", rec, fatal=False)
        return False


async def read(ctx: ChangeContext, tree: Tree, path: str) -> str | None:
    try:
        return (await tree.read_text(path)).content
    except FileNotFoundError:
        return None


async def refresh(ctx: ChangeContext, rec: ChangeRecord, paths: set[str]) -> None:
    for path in sorted(paths):
        modified = await read(ctx, rec.tree, path)
        if modified is None:
            rec.head_files.pop(path, None)
        else:
            rec.head_files[path] = modified
    rec.head = await parse(rec.head_files, rec.base_commit, rec.head)
    rec.graph = change_graph(rec.base, rec.head)


async def update(ctx: ChangeContext, rec: ChangeRecord, path: str) -> None:
    rec.observed_paths.add(path)
    rec.touched.add(path)
    previous = rec.head_files.get(path)
    await refresh(ctx, rec, {path})
    await send_file(ctx, rec, path, previous=previous)


async def totals(ctx: ChangeContext, rec: ChangeRecord) -> tuple[int, int, int]:
    files = added = removed = 0
    for path in rec.base_files.keys() | rec.head_files.keys():
        original, modified = rec.base_files.get(path), rec.head_files.get(path)
        if original != modified:
            plus, minus = await count_lines_async(original, modified)
            files, added, removed = files + 1, added + plus, removed + minus
    return files, added, removed


async def send_file(
    ctx: ChangeContext,
    rec: ChangeRecord,
    path: str,
    author: By = "agent",
    *,
    previous: str | None = None,
) -> None:
    original, modified = rec.base_files.get(path), rec.head_files.get(path)
    status = file_status(original, modified)
    if status is None:  # in neither version: nothing to show
        rec.unpublished.discard(path)
        return
    added, removed = await count_lines_async(original, modified)
    files, total_added, total_removed = await totals(ctx, rec)
    payload = ChangeUpdated(
        change_id=rec.change_id,
        agent_id=rec.agent_id,
        author=author,
        file=FileDiff(
            path=path,
            status=status,
            original=original,
            modified=modified,
            lines_added=added,
            lines_removed=removed,
        ),
        node_ids=nodes_in_path(rec.graph, rec.base.graph, rec.head.graph, path),
        graph=rec.graph,
        files_changed=files,
        lines_added=total_added,
        lines_removed=total_removed,
    )
    await ctx.whole(_record_file(ctx, rec, payload, previous))


async def _record_file(
    ctx: ChangeContext, rec: ChangeRecord, payload: ChangeUpdated, previous: str | None
) -> None:
    event = await ctx.publish(payload)
    stored = receipt(event, payload)
    if stored is not None and previous is not None:
        rec.run_edits.append(EditReceipt(stored, rec.revision + 1, previous))
    rec.published[payload.file.path] = payload.file.modified
    rec.unpublished.discard(payload.file.path)


async def proposed(ctx: ChangeContext, agent_id: AgentId, summary: str | None) -> bool:
    """Commit, republish, test and send `change.proposed`; `False` after a fatal `error`."""
    rec = ctx.state.agents.get(agent_id)
    try:
        if rec is None or rec.state != "working":
            raise WrongState(f"{agent_id} has no change in progress")
        async with ctx.workspace.locks(main=True, agent_id=agent_id):
            if rec.requested is not None:
                # R2: revision n + 1 never precedes its request in the log.
                failure = await delivery.send_requested(ctx, rec)
                if failure:
                    raise RuntimeError(f"its request for changes is still unsent ({failure})")
            head_commit, files = await commit(ctx, rec)
            await proposal(ctx, rec, head_commit, files, "agent", summary)
        return True
    except Exception as error:
        source: Source = "git" if isinstance(error, WorkspaceError) else "server"
        change = rec.change_id if rec else agent_id
        await ctx.error(source, f"{change} could not be proposed: {error}", rec, fatal=True)
        return False


async def proposal(
    ctx: ChangeContext,
    rec: ChangeRecord,
    head: str,
    files: dict[str, FileStatus],
    author: By,
    summary: str | None,
) -> None:
    """Republish what differs from what the UI holds, test the worktree, take the cone on the
    agent's version, then publish `change.proposed` (revision n + 1) and record it in one
    `_whole` step. The caller holds the agent's lock and has committed `head`."""
    paths = set(files) | rec.touched | rec.unpublished
    await refresh(ctx, rec, paths)
    for path in sorted(paths):
        modified = rec.head_files.get(path)
        fresh = path in rec.published and rec.published[path] == modified
        if path in rec.unpublished or not fresh:
            await send_file(ctx, rec, path, author)
    tests = await run_tests(ctx, rec)
    limits = rec.task.limits
    impact = change_cone(
        rec.graph,
        rec.head.graph,
        max_depth=limits.cone_max_depth,
        max_nodes=limits.cone_max_nodes,
    )
    stats: list[FileStat] = []
    for path, status in sorted(files.items()):
        plus, minus = await count_lines_async(rec.base_files.get(path), rec.head_files.get(path))
        stats.append(FileStat(path=path, status=status, lines_added=plus, lines_removed=minus))
    proposal = ChangeProposed(
        change_id=rec.change_id,
        agent_id=rec.agent_id,
        revision=rec.revision + 1,
        task=rec.task.task,
        examples=task_examples(rec.task.examples),
        summary=summary,
        base_commit=rec.base_commit,
        head_commit=head,
        files=stats,
        lines_added=sum(s.lines_added for s in stats),
        lines_removed=sum(s.lines_removed for s in stats),
        graph=rec.graph,
        tests=tests,
        impact=impact,
    )
    evidence = await capture_facts(ctx, rec, head=head, revision=proposal.revision)
    interrupted = False
    try:
        await ctx.whole(record_proposal(ctx, rec, proposal))
    except asyncio.CancelledError:
        if rec.revision != proposal.revision:
            raise
        interrupted = True
    await record_probe_facts(
        ctx,
        rec,
        head,
        proposal.revision,
        evidence,
        capture=ReviewCapture(evidence.target, rec.tree),
        proposal=proposal,
        author=author,
        interrupted=interrupted,
    )


async def record_probe_facts(
    ctx: ChangeContext,
    rec: ChangeRecord,
    head: str,
    revision: int,
    evidence: FactsEvidence,
    *,
    interrupted: bool,
    capture: ReviewCapture,
    proposal: ChangeProposed,
    author: By,
) -> None:
    cancelled = False
    try:
        if interrupted:
            raise asyncio.CancelledError
        probes, problems = await probe_added_tests(ctx, rec, head=head, revision=revision)
    except asyncio.CancelledError:
        ids, diagnostics = await asyncio.to_thread(added_cases, rec, revision)
        probes = ()
        problems = (*diagnostics, "added tests: probes interrupted") if ids or diagnostics else ()
        cancelled = True
    except Exception as error:
        probes, problems = (), (f"added tests: probes unavailable ({type(error).__name__})",)
    if ctx.behavior_diff:
        try:
            if interrupted or cancelled:
                raise asyncio.CancelledError
            rec.behavior_diffs[revision] = await probe_behavior(
                ctx, rec, head=head, revision=revision
            )
        except asyncio.CancelledError:
            rec.behavior_diffs[revision] = unavailable_behavior(rec, revision, "interrupted")
            cancelled = True
        except Exception:
            rec.behavior_diffs[revision] = unavailable_behavior(rec, revision, "worker failed")
    if ctx.measured_cone:
        try:
            if interrupted or cancelled:
                raise asyncio.CancelledError
            rec.measured_cones[revision] = await measure_cone(
                ctx, rec, target=evidence.target, impact=proposal.impact
            )
        except asyncio.CancelledError:
            rec.measured_cones.setdefault(
                revision,
                unavailable_measured(
                    evidence.target, proposal.impact, "caller measurement interrupted"
                ),
            )
            cancelled = True
        except Exception as error:
            rec.measured_cones[revision] = unavailable_measured(
                evidence.target,
                proposal.impact,
                f"caller measurement unavailable ({type(error).__name__})",
            )
    evidence = replace(
        evidence,
        behavior_evidence=capture_behavior(rec.behavior_diffs.get(revision))
        if ctx.behavior_diff
        else None,
        measured=rec.measured_cones.get(revision),
        impact=proposal.impact.model_copy(deep=True),
        added_tests=probes,
        problems=evidence.problems + problems,
        executions=deepcopy(rec.executions),
        runs=deepcopy(rec.suite_runs),
    )
    await ctx.whole(publish_facts(ctx, evidence))
    if cancelled:
        raise asyncio.CancelledError
    if not interrupted and ctx.ports.review is not None:
        ctx.ports.review.facts_ready(capture, proposal, author=author)


async def publish_facts(ctx: ChangeContext, evidence: FactsEvidence) -> None:
    if ctx.cards is not None and (ctx.behavior_diff or ctx.measured_cone):
        ctx.cards.prepare(ctx.state.changes[evidence.target.change_id], evidence)
    facts = await asyncio.to_thread(build_change_facts, evidence)
    await ctx.publish(facts)
    ctx.assessment.facts_ready(ctx.state.changes[evidence.target.change_id], evidence)


async def record_proposal(ctx: ChangeContext, rec: ChangeRecord, proposal: ChangeProposed) -> None:
    """R7: a run cancelled during this publish still finds the record matching the store."""
    mode = ctx.mode.snapshot()
    armed = not ctx.mode.authority_suspended()
    ctx.assessment.invalidate(rec)
    rec.invalidate_cards()
    await ctx.publish(proposal)
    rec.revision, rec.head_commit = proposal.revision, proposal.head_commit
    rec.proposed_now, rec.summary = True, proposal.summary
    ctx.assessment.proposed(rec, proposal, mode, armed=armed)


async def commit(
    ctx: ChangeContext, rec: ChangeRecord, by: By = "agent"
) -> tuple[str, dict[str, FileStatus]]:
    """Commit the worktree as the agent, or as the user for edits in review; HEAD, and the
    files that differ from the base."""
    tree = rec.tree
    await ctx.workspace.veto_index(tree, timeout_s=ctx.timeout)
    await ctx.git(tree, "add", "-A")
    if (await ctx.git(tree, "status", "--porcelain")).strip():
        if by == "agent":
            message, author = f"{rec.change_id} ({rec.agent_id})", rec.agent_id
        else:
            message, author = f"{rec.change_id}: edited in review", "user"
        await ctx.workspace.veto_index(tree, timeout_s=ctx.timeout)
        await ctx.git(tree, "commit", "-q", "-m", message, commit_as=author)
    head = (await ctx.git(tree, "rev-parse", "HEAD")).strip()
    names = await ctx.git(
        tree, "diff", "--name-status", "--no-renames", "-z", rec.base_commit, head
    )
    return head, parse_name_status(names)


async def run_tests(ctx: ChangeContext, rec: ChangeRecord) -> SuiteResult:
    try:
        run = await ctx.workspace.run_tests(rec.tree, timeout_s=rec.task.limits.test_timeout_s)
    except SuiteInterrupted as error:
        await publish_suite(ctx, rec, error.run, phase="proposal", revision=rec.revision + 1)
        raise
    except (WorkspaceError, OSError) as error:
        if isinstance(error, SuiteUnavailable):
            await publish_suite(ctx, rec, error.run, phase="proposal", revision=rec.revision + 1)
        await ctx.error("tests", f"The tests could not run: {error}", rec, fatal=False)
        return SuiteResult(
            outcome="error", passed=0, failed=0, skipped=0, duration_ms=0, failed_tests=[]
        )
    try:
        run = await verify_run(ctx, rec, run, phase="proposal")
    except asyncio.CancelledError:
        await publish_suite(
            ctx, rec, unverified_run(rec, run), phase="proposal", revision=rec.revision + 1
        )
        raise
    execution = await publish_suite(ctx, rec, run, phase="proposal", revision=rec.revision + 1)
    return execution.suite


def run_ended(
    ctx: ChangeContext, agent_id: AgentId, end: RunEnd | None, *, steps: int | None = None
) -> None:
    """The run is over (`end` is `None` when it raised or was cancelled): `ready` if this run
    proposed, else `unproposed`. `steps`, the run's own count, wins over `end.steps`. A run
    that a Stop cancelled finds its change `deciding`: that outcome becomes the Stop's
    `fallback`, where a Stop that ends without settling leaves the change."""
    rec = ctx.state.agents.get(agent_id)
    if rec is None:
        raise ChangeNotFound(agent_id)
    stopping = rec.state == "deciding" and rec.fallback == "working"
    if rec.state != "working" and not stopping:
        return
    if steps is not None:
        rec.steps = steps
    elif end is not None:
        rec.steps = end.steps
    rec.review_ready = end is not None and end.state == "ready_for_review"
    outcome: State = "ready" if rec.proposed_now else "unproposed"
    if stopping:
        rec.fallback = outcome
    else:
        rec.state = outcome
        ctx.assessment.wake(rec)


def unreported(ctx: ChangeContext, rec: ChangeRecord) -> None:
    """Settle a run that will never report its end, as `run_ended` does for one that did: none
    was passed to a Stop, or the one passed was cancelled before its first step and so never
    ran its `finally`. Without it a Stop cancelled before its body, or in its wait for the run,
    leaves the change `working` with no run. A no-op once a run has reported."""
    run_ended(ctx, rec.agent_id, None)
