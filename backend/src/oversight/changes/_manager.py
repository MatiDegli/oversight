"""The change manager: one record per agent's change, from its worktree to a merge or a discard.

A change is `working` until its run ends, then `ready` (proposed) or `unproposed` (no proposal,
such as after a halt whose worktree root was lost). Approve takes `ready`; Reject takes `ready`,
`conflict`, `unproposed` or `broken`. Both enter `deciding`, which always ends: in `closing` once
the decision is a fact, in `conflict`, back in `ready` when main is untouched, or in `broken` when
main's state cannot be read. `closing` becomes `closed` once its resolution, delta and `done` are
all sent; until then the change is pending, keeps its worktree until the resolution is out, and the
next decision or `open` sends the rest. Every git call is `Workspace.git`, async with a timeout;
approve and reject hold main's lock, then the agent's, throughout.
"""

import asyncio
import functools
import os
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any, Literal

from oversight.agents import AgentTask, Publish, RunEnd
from oversight.changes._diff import count_lines, file_status, parse_name_status
from oversight.changes._versions import main_delta, nodes_in_path, parse, with_positions
from oversight.events import Clock, EventPayload, system_clock
from oversight.graph import CodeVersion, WorkspaceGraph, change_cone, change_graph
from oversight.workspace import ProcessResult, Tree, Workspace, WorkspaceError
from oversight_contracts.common import AgentId, ChangeId, FileStatus
from oversight_contracts.events import (
    AgentStatus,
    ChangeProposed,
    ChangeResolved,
    ChangeUpdated,
    Conflict,
    ErrorReport,
    FileDiff,
    FileStat,
    SuiteResult,
)
from oversight_contracts.graph import ChangeGraph, GraphDelta, GraphSnapshot

type State = Literal[
    "working", "ready", "unproposed", "deciding", "conflict", "closing", "closed", "broken"
]
type Source = Literal["git", "tests", "graph", "server"]

APPROVABLE: frozenset[State] = frozenset({"ready"})
REJECTABLE: frozenset[State] = frozenset({"ready", "conflict", "unproposed", "broken"})
"""`broken`: the merge failed and main's state is unknown; Reject still closes the change."""
CLOSE_OUT_ATTEMPTS = 2
"""Tries per occasion (a decision, a retry before the next decision or `open`)."""


class ChangeNotFound(LookupError):
    """No change has that id: the route answers not_found."""


class WrongState(Exception):
    """The change cannot take that decision now: the route answers wrong_state."""


@dataclass
class _Change:
    task: AgentTask
    model_id: str
    tree: Tree
    base_commit: str
    base_files: dict[str, str]
    base: CodeVersion
    head_files: dict[str, str]
    head: CodeVersion
    graph: ChangeGraph
    state: State = "working"
    revision: int = 0
    head_commit: str | None = None
    steps: int = 0
    merged: bool = False
    touched: set[str] = field(default_factory=lambda: set[str]())
    published: dict[str, str | None] = field(default_factory=lambda: dict[str, str | None]())
    """Per path, the `modified` last sent in a `change.updated`."""
    unpublished: set[str] = field(default_factory=lambda: set[str]())
    resolution: ChangeResolved | None = None
    delta: GraphDelta | None = None
    next_snapshot: GraphSnapshot | None = None
    sent: set[str] = field(default_factory=lambda: set[str]())
    """Close-out events already sent: "resolved", "delta", "done"."""
    sending: asyncio.Task[None] | None = None
    """The close-out publish in flight, kept until it ends, even when its close-out was cancelled;
    the next close-out, and any merge, waits for it first."""
    cleaned: bool = False
    """The worktree and branch are gone."""
    fallback: State = "ready"
    """Where a decision that ends without settling leaves the change: the state it was taken from,
    or `broken` once the merge may have touched main."""
    decision: int = 0
    """Counts the decisions taken; only the latest one's task may end `deciding`."""

    @property
    def agent_id(self) -> AgentId:
        return self.task.agent_id

    @property
    def change_id(self) -> ChangeId:
        return self.task.change_id


@dataclass(frozen=True)
class _Facts:
    """Main as git reports it after a merge attempt."""

    head: str
    clean: bool
    merging: bool
    contains: bool
    """The change's head commit is an ancestor of main's HEAD."""

    @property
    def merged(self) -> bool:
        return self.contains and self.clean and not self.merging

    def unchanged(self, pre: str | None) -> bool:
        # No `pre`: the first git call failed, so the merge never ran.
        return (pre is None or self.head == pre) and self.clean and not self.merging


class ChangeManager:
    """Implements the runtime's change sink and the decisions. One instance per run."""

    def __init__(
        self,
        workspace: Workspace,
        publish: Publish,
        *,
        main_graph: WorkspaceGraph,
        main_snapshot: GraphSnapshot,
        clock: Clock = system_clock,
        git_timeout_s: float = 30,
    ) -> None:
        self._workspace = workspace
        self._publish = publish
        self._main_graph = main_graph
        self.snapshot = main_snapshot
        """Main's graph as last published."""
        self._clock = clock
        self._timeout = git_timeout_s
        self._changes: dict[ChangeId, _Change] = {}
        self._agents: dict[AgentId, _Change] = {}
        self._bases: dict[str, tuple[dict[str, str], CodeVersion]] = {}
        self._pending: dict[AgentId, _Change] = {}
        """Changes whose close-out or cleanup is unfinished; the next decision or `open` runs them
        again first."""
        self._tasks: set[asyncio.Task[None]] = set()

    # --- Helpers ------------------------------------------------------------------------------

    def _date(self) -> str:
        return self._clock().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    async def _run(self, tree: Tree, *args: str) -> ProcessResult:
        return await self._workspace.git(tree, *args, timeout_s=self._timeout)

    async def _git(self, tree: Tree, *args: str, commit_as: str | None = None) -> str:
        """`git_ok` with the manager's timeout; a commit gets an author and the clock's date."""
        if commit_as is None:
            return await self._workspace.git_ok(tree, *args, timeout_s=self._timeout)
        workspace = self._workspace
        author = (
            workspace.user_author() if commit_as == "user" else workspace.agent_author(commit_as)
        )
        return await workspace.git_ok(
            tree, *args, timeout_s=self._timeout, author=author, date=self._date()
        )

    async def _yes_no(self, tree: Tree, *args: str) -> bool:
        """A git query that answers with its exit code: 0 yes, 1 no, anything else fails."""
        result = await self._run(tree, *args)
        if result.timed_out or result.returncode not in (0, 1):
            raise WorkspaceError(f"git {args[0]} failed: {self._workspace.redact(result.stderr)}")
        return result.returncode == 0

    async def _error(
        self, source: Source, message: str, rec: _Change | None, *, fatal: bool
    ) -> None:
        report = ErrorReport(
            source=source,
            message=self._workspace.redact(message)[:1000],
            agent_id=rec.agent_id if rec else None,
            change_id=rec.change_id if rec else None,
            fatal=fatal,
        )
        try:
            await self._publish(report)
        except Exception:
            return  # nowhere left to report it

    def _spawn(self, work: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # --- Opening a change ---------------------------------------------------------------------

    async def open(self, task: AgentTask, model_id: str) -> Tree:
        """Create the agent's worktree from main's HEAD and record its base files and graph."""
        if task.change_id in self._changes or task.agent_id in self._agents:
            raise WrongState(f"{task.agent_id} or {task.change_id} was already opened")
        await self._finish_pending()
        tree = await self._workspace.create_worktree(task.agent_id)
        base_commit = tree.base_commit
        if base_commit is None:
            raise WorkspaceError(f"{task.agent_id}'s worktree has no base commit")
        async with self._workspace.locks(agent_id=task.agent_id):
            files, base = self._bases.get(base_commit) or await self._read_base(tree, base_commit)
        rec = _Change(
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
        self._changes[task.change_id] = rec
        self._agents[task.agent_id] = rec
        return tree

    async def _read_base(self, tree: Tree, commit: str) -> tuple[dict[str, str], CodeVersion]:
        files: dict[str, str] = {}
        for path in await tree.list_files():
            try:
                files[path] = (await tree.read_text(path)).content
            except WorkspaceError:
                continue  # not UTF-8 text: no agent tool can edit it
        found = (files, await parse(files, commit, None))
        self._bases[commit] = found
        return found

    # --- The live diff ------------------------------------------------------------------------

    async def file_edited(self, agent_id: AgentId, path: str) -> bool:
        """Send `change.updated` for an edit on disk; `False` after sending an `error` instead."""
        rec = self._agents.get(agent_id)
        try:
            if rec is None or rec.state != "working":
                raise WrongState(f"{agent_id} has no change in progress")
            async with self._workspace.locks(agent_id=agent_id):
                for earlier in sorted(rec.unpublished - {path}):
                    try:
                        await self._update(rec, earlier)
                    except Exception as error:
                        await self._error("server", f"{earlier}: {error}", rec, fatal=False)
                await self._update(rec, path)
            return True
        except Exception as error:
            if rec is not None:
                rec.unpublished.add(path)
            await self._error(
                "server", f"The edit of {path} was not shown: {error}", rec, fatal=False
            )
            return False

    async def _read(self, tree: Tree, path: str) -> str | None:
        try:
            return (await tree.read_text(path)).content
        except FileNotFoundError:
            return None

    async def _refresh(self, rec: _Change, paths: set[str]) -> None:
        for path in sorted(paths):
            modified = await self._read(rec.tree, path)
            if modified is None:
                rec.head_files.pop(path, None)
            else:
                rec.head_files[path] = modified
        rec.head = await parse(rec.head_files, rec.base_commit, rec.head)
        rec.graph = change_graph(rec.base, rec.head)

    async def _update(self, rec: _Change, path: str) -> None:
        rec.touched.add(path)
        await self._refresh(rec, {path})
        await self._send_file(rec, path)

    def _totals(self, rec: _Change) -> tuple[int, int, int]:
        files = added = removed = 0
        for path in rec.base_files.keys() | rec.head_files.keys():
            original, modified = rec.base_files.get(path), rec.head_files.get(path)
            if original != modified:
                plus, minus = count_lines(original, modified)
                files, added, removed = files + 1, added + plus, removed + minus
        return files, added, removed

    async def _send_file(self, rec: _Change, path: str) -> None:
        original, modified = rec.base_files.get(path), rec.head_files.get(path)
        status = file_status(original, modified)
        if status is None:  # in neither version: nothing to show
            rec.unpublished.discard(path)
            return
        added, removed = count_lines(original, modified)
        files, total_added, total_removed = self._totals(rec)
        await self._publish(
            ChangeUpdated(
                change_id=rec.change_id,
                agent_id=rec.agent_id,
                author="agent",
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
        )
        rec.published[path] = modified
        rec.unpublished.discard(path)

    # --- The proposal -------------------------------------------------------------------------

    async def proposed(self, agent_id: AgentId, summary: str | None) -> bool:
        """Commit, republish, test and send `change.proposed`; `False` after a fatal `error`."""
        rec = self._agents.get(agent_id)
        try:
            if rec is None or rec.state != "working":
                raise WrongState(f"{agent_id} has no change in progress")
            async with self._workspace.locks(agent_id=agent_id):
                head_commit, files = await self._commit(rec)
                await self._refresh(rec, set(files) | rec.touched)
                for path in sorted(set(files) | rec.touched):
                    modified = rec.head_files.get(path)
                    if path not in rec.published or rec.published[path] != modified:
                        await self._send_file(rec, path)
                tests = await self._run_tests(rec)
                limits = rec.task.limits
                impact = change_cone(
                    rec.graph,
                    rec.head.graph,
                    max_depth=limits.cone_max_depth,
                    max_nodes=limits.cone_max_nodes,
                )
                stats = [
                    FileStat(path=path, status=status, lines_added=plus, lines_removed=minus)
                    for path, status in sorted(files.items())
                    for plus, minus in [
                        count_lines(rec.base_files.get(path), rec.head_files.get(path))
                    ]
                ]
                await self._publish(
                    ChangeProposed(
                        change_id=rec.change_id,
                        agent_id=agent_id,
                        revision=rec.revision + 1,
                        task=rec.task.task,
                        summary=summary,
                        base_commit=rec.base_commit,
                        head_commit=head_commit,
                        files=stats,
                        lines_added=sum(s.lines_added for s in stats),
                        lines_removed=sum(s.lines_removed for s in stats),
                        graph=rec.graph,
                        tests=tests,
                        impact=impact,
                    )
                )
                rec.revision += 1
                rec.head_commit = head_commit
            return True
        except Exception as error:
            source: Source = "git" if isinstance(error, WorkspaceError) else "server"
            change = rec.change_id if rec else agent_id
            await self._error(source, f"{change} could not be proposed: {error}", rec, fatal=True)
            return False

    async def _commit(self, rec: _Change) -> tuple[str, dict[str, FileStatus]]:
        """Commit the worktree as the agent; HEAD, and the files that differ from the base."""
        tree = rec.tree
        await self._git(tree, "add", "-A")
        if (await self._git(tree, "status", "--porcelain")).strip():
            message = f"{rec.change_id} ({rec.agent_id})"
            await self._git(tree, "commit", "-q", "-m", message, commit_as=rec.agent_id)
        head = (await self._git(tree, "rev-parse", "HEAD")).strip()
        names = await self._git(
            tree, "diff", "--name-status", "--no-renames", "-z", rec.base_commit, head
        )
        return head, parse_name_status(names)

    async def _run_tests(self, rec: _Change) -> SuiteResult:
        try:
            run = await self._workspace.run_tests(
                rec.tree, timeout_s=rec.task.limits.test_timeout_s
            )
        except (WorkspaceError, OSError) as error:
            await self._error("tests", f"The tests could not run: {error}", rec, fatal=False)
            return SuiteResult(
                outcome="error", passed=0, failed=0, skipped=0, duration_ms=0, failed_tests=[]
            )
        return run.result

    def run_ended(self, agent_id: AgentId, end: RunEnd | None) -> None:
        """The run is over (`None`: it raised): `ready` if it was proposed, else `unproposed`."""
        rec = self._agents.get(agent_id)
        if rec is None:
            raise ChangeNotFound(agent_id)
        if rec.state != "working":
            return
        if end is not None:
            rec.steps = end.steps
        rec.state = "ready" if rec.head_commit is not None else "unproposed"

    # --- Decisions ----------------------------------------------------------------------------

    def _take(self, change_id: ChangeId, allowed: frozenset[State], verb: str) -> _Change:
        """Check and flip the state with no `await`, so a second click fails at once."""
        rec = self._changes.get(change_id)
        if rec is None:
            raise ChangeNotFound(change_id)
        if rec.state not in allowed:
            raise WrongState(
                f"{change_id} is {rec.state}; {verb} needs {' or '.join(sorted(allowed))}"
            )
        rec.fallback, rec.state = rec.state, "deciding"
        rec.decision += 1
        return rec

    def _decide(self, rec: _Change, work: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
        """Run a decision in a tracked task. A decision that ends still `deciding`, such as a task
        cancelled before its first step, which runs none of its code, goes back to `fallback`
        through a done-callback, which runs before anyone awaiting the task resumes. The callback
        keeps its decision's number: a decision that settled, then took a while to report it, ends
        after the next one was taken, and must leave that one `deciding`."""
        task = self._spawn(work)
        decision = rec.decision
        task.add_done_callback(lambda _: self._restore(rec, decision))
        return task

    @staticmethod
    def _restore(rec: _Change, decision: int) -> None:
        if rec.state == "deciding" and rec.decision == decision:
            rec.state = rec.fallback

    def approve(self, change_id: ChangeId, comment: str | None) -> asyncio.Task[None]:
        rec = self._take(change_id, APPROVABLE, "approve")
        return self._decide(rec, self._approve(rec, comment))

    def reject(self, change_id: ChangeId, comment: str | None) -> asyncio.Task[None]:
        rec = self._take(change_id, REJECTABLE, "reject")
        return self._decide(rec, self._reject(rec, comment))

    async def aclose(self) -> None:
        """Wait for the decisions in flight."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _approve(self, rec: _Change, comment: str | None) -> None:
        await self._finish_pending()
        async with self._workspace.locks(main=True, agent_id=rec.agent_id):
            # A close-out publish left running by a cancel may still move `snapshot`, which this
            # merge's delta starts from, and must find its own delta still there: wait for it.
            for other in list(self._pending.values()):
                await self._drain(other)
            pre: str | None = None
            try:
                pre = (await self._git(self._workspace.main, "rev-parse", "HEAD")).strip()
                await self._merge(rec, comment, pre)
            except asyncio.CancelledError:
                # Still under both locks: settle main from its facts, then cancel.
                await self._recover(rec, comment, pre, None)
                raise
            except Exception as error:
                await self._recover(rec, comment, pre, error)
            if rec.merged:
                await self._finish(rec)

    async def _reject(self, rec: _Change, comment: str | None) -> None:
        await self._finish_pending()
        async with self._workspace.locks(main=True, agent_id=rec.agent_id):
            rec.resolution = self._resolution(rec, "rejected", comment)
            await self._finish(rec)  # main did not change: no rescan and no delta

    def _resolution(
        self,
        rec: _Change,
        decision: Literal["approved", "rejected"],
        comment: str | None,
        *,
        merge_commit: str | None = None,
        conflict: Conflict | None = None,
    ) -> ChangeResolved:
        merge: Literal["merged", "conflict"] | None = None
        if decision == "approved":
            merge = "conflict" if conflict is not None else "merged"
        return ChangeResolved(
            change_id=rec.change_id,
            agent_id=rec.agent_id,
            revision=rec.revision,
            decision=decision,
            reason=None,
            comment=comment,
            merge=merge,
            merge_commit=merge_commit,
            conflict=conflict,
        )

    async def _merge(self, rec: _Change, comment: str | None, pre: str) -> None:
        main = self._workspace.main
        head = rec.head_commit or rec.base_commit
        rec.fallback = "broken"  # from here on, main may change
        # --no-ff on every merge: --no-commit alone fast-forwards a head that descends from main,
        # leaving HEAD moved and nothing for `merge --abort` to undo.
        result = await self._run(main, "merge", "--no-ff", "--no-commit", head)
        if result.returncode != 0:
            if (
                result.returncode == 1
                and not result.timed_out
                and await self._conflict(rec, comment, pre)
            ):
                return
            detail = "timed out" if result.timed_out else self._workspace.redact(result.stderr)
            raise WorkspaceError(f"git merge failed: {detail.strip()[-500:]}")
        if await self._yes_no(main, "rev-parse", "-q", "--verify", "MERGE_HEAD"):
            await self._git(
                main,
                "commit",
                "-q",
                "-m",
                f"Merge {rec.change_id} ({rec.agent_id})",
                commit_as="user",
            )
        facts = await self._facts(head)
        if not facts.merged:
            raise WorkspaceError("the merge did not land on main")
        await self._record_merge(rec, comment, facts.head)

    async def _conflict(self, rec: _Change, comment: str | None, pre: str) -> bool:
        main = self._workspace.main
        names = await self._git(main, "diff", "--name-only", "--diff-filter=U", "-z")
        paths = sorted(p for p in names.split("\0") if p)
        if not paths:
            return False
        await self._git(main, "merge", "--abort")
        if not (await self._facts(rec.head_commit or rec.base_commit)).unchanged(pre):
            raise WorkspaceError("main did not return to its HEAD after the conflict")
        conflict = Conflict(kind="merge", paths=paths, tests=None)
        await self._publish(self._resolution(rec, "approved", comment, conflict=conflict))
        rec.state = "conflict"
        return True

    async def _facts(self, head_commit: str) -> _Facts:
        main = self._workspace.main
        return _Facts(
            head=(await self._git(main, "rev-parse", "HEAD")).strip(),
            clean=not (await self._git(main, "status", "--porcelain")).strip(),
            merging=await self._yes_no(main, "rev-parse", "-q", "--verify", "MERGE_HEAD"),
            contains=await self._yes_no(main, "merge-base", "--is-ancestor", head_commit, "HEAD"),
        )

    async def _recover(
        self, rec: _Change, comment: str | None, pre: str | None, error: Exception | None
    ) -> None:
        """Decide from git facts after a failed approval, or a cancelled one (`error` is `None`):
        abort an unfinished merge, record a landed one. No `reset --hard`, no lock removal. A
        cancelled approval always reports; a landed merge closes out first and leaves its cleanup
        to the next decision or `open`."""
        main = self._workspace.main
        facts: _Facts | None = None
        try:
            if await self._yes_no(main, "rev-parse", "-q", "--verify", "MERGE_HEAD"):
                await self._git(main, "merge", "--abort")
            facts = await self._facts(rec.head_commit or rec.base_commit)
        except Exception:
            facts = None
        if facts is not None and facts.merged:
            await self._record_merge(rec, comment, facts.head)
            if error is not None:
                return  # the approval closes out and cleans up
            await self._close_out(rec)
            note = "the merge landed and is recorded"
        elif facts is not None and facts.unchanged(pre):
            rec.state = "ready"
            note = "main is unchanged; approve again or reject"
        else:
            rec.state = "broken"
            note = "main's state is unknown; reject the change or restart"
        what = "was cancelled" if error is None else f"failed: {error}"
        await self._error("git", f"Approving {rec.change_id} {what}. {note}.", rec, fatal=False)

    async def _record_merge(self, rec: _Change, comment: str | None, post: str) -> None:
        """The merge is a fact: rescan main and build the resolution and delta, sending nothing."""
        try:
            new = await asyncio.to_thread(self._main_graph.scan, post)
        except Exception as error:
            # The snapshot stays, so the next delta starts from what the UI holds.
            await self._error("graph", f"Main's graph was not rescanned: {error}", rec, fatal=False)
        else:
            rec.delta = main_delta(self.snapshot, new, post)
            rec.next_snapshot = with_positions(new, self.snapshot)
            for other in self._pending.values():
                if "delta" not in other.sent:
                    # This delta starts from the graph the UI holds and covers that merge too;
                    # sent later, the older one would start from a graph the UI no longer holds.
                    other.delta = other.next_snapshot = None
        rec.merged = True
        rec.resolution = self._resolution(rec, "approved", comment, merge_commit=post)

    def _done(self, rec: _Change) -> AgentStatus:
        task = rec.task
        return AgentStatus(
            agent_id=task.agent_id,
            slot=task.slot,
            change_id=task.change_id,
            task=task.task,
            model=rec.model_id,
            state="done",
            step=rec.steps,
            max_steps=task.limits.max_steps,
            detail=None,
        )

    async def _finish(self, rec: _Change) -> None:
        """Close out, then clean up once `change.resolved` is out, so a worktree or branch is never
        removed before its resolution is sent. The change stays pending until both are done."""
        async with self._workspace.locks(main=True, agent_id=rec.agent_id):
            await self._close_out(rec)
            if "resolved" in rec.sent and not rec.cleaned:
                await self._cleanup(rec)
            if rec.state == "closed" and rec.cleaned:
                self._pending.pop(rec.agent_id, None)

    async def _finish_pending(self) -> None:
        """Before a decision or an `open`: retry every unfinished close-out and cleanup."""
        for rec in list(self._pending.values()):
            await self._finish(rec)

    async def _close_out(self, rec: _Change) -> None:
        """The only way into `closed`: the resolution, the delta (approve only), then `done`, each
        marked when sent, so nothing goes twice. Until all are out the change is `closing` and
        pending, and a later call sends only what is missing. Never raises, save a cancel."""
        rec.state = "closing"
        self._pending[rec.agent_id] = rec
        failure = ""
        for _ in range(CLOSE_OUT_ATTEMPTS):
            failure = await self._send_close_out(rec)
            if not failure:
                rec.state = "closed"
                return
        await self._error(
            "server",
            f"The close-out of {rec.change_id} is incomplete ({failure}). "
            "The next decision or agent start sends the rest.",
            rec,
            fatal=False,
        )

    async def _send_close_out(self, rec: _Change) -> str:
        """Send what is missing, in order; the first failure, or "" once everything is out. A
        publish that a cancelled close-out left running is waited for first, so it is not resent."""
        await self._drain(rec)
        events: list[tuple[str, EventPayload | None]] = [
            ("resolved", rec.resolution),
            ("delta", rec.delta),
            ("done", self._done(rec)),
        ]
        for name, payload in events:
            if name in rec.sent or payload is None:
                continue
            # The publish runs in its own task, kept on the record, and a done-callback records its
            # outcome: the store may commit the event however many cancels the close-out gets, and
            # the next close-out must not send it again.
            sending = self._spawn(self._deliver(payload))
            sending.add_done_callback(functools.partial(self._receipt, rec, name))
            rec.sending = sending
            cancelled = await self._settled(sending)
            if cancelled or sending.cancelled():
                raise asyncio.CancelledError
            failure = sending.exception()
            if failure is not None:
                return f"{name}: {failure}"
        return ""

    async def _deliver(self, payload: EventPayload) -> None:
        """`_publish` as a tracked task's coroutine."""
        await self._publish(payload)

    def _receipt(self, rec: _Change, name: str, sending: asyncio.Task[None]) -> None:
        """A close-out publish ended: mark its event sent if the store took it, and move `snapshot`
        after a delta. As a done-callback added first, it runs before anyone awaiting the publish
        resumes, cancelled or not. A cancelled publish tells nothing: its event stays unsent."""
        if rec.sending is sending:
            rec.sending = None
        if sending.cancelled() or sending.exception() is not None:
            return
        rec.sent.add(name)
        if name == "delta" and rec.next_snapshot is not None:
            self.snapshot = rec.next_snapshot

    @staticmethod
    async def _drain(rec: _Change) -> None:
        """Wait for `rec`'s close-out publish in flight, if any; its outcome is recorded when this
        returns. A cancel ends the wait and leaves the publish on the record."""
        if rec.sending is not None:
            await asyncio.wait([rec.sending])

    @staticmethod
    async def _settled(task: asyncio.Task[None]) -> bool:
        """Wait for `task` through one cancel of the caller: True if one came, for the caller to
        re-raise once the outcome is recorded. A second cancel ends the wait; `task` runs on, kept
        on the record, records its own outcome, and the next close-out or merge waits for it."""
        try:
            await asyncio.wait([task])
        except asyncio.CancelledError:
            await asyncio.wait([task])
            return True
        return False

    # --- Cleanup ------------------------------------------------------------------------------

    async def _cleanup(self, rec: _Change) -> None:
        """Remove the worktree, then the branch, each step skipped once done, so a rerun after a
        failure finishes the job. A failure is reported and retried by the next decision or
        `open`, as the change is still pending."""
        workspace, agent = self._workspace, rec.agent_id
        main = workspace.main
        try:
            async with workspace.locks(main=True, agent_id=agent):
                listing = await self._git(main, "worktree", "list", "--porcelain", "-z")
                if f"worktree {rec.tree.root}" in listing.split("\0"):
                    if await asyncio.to_thread(os.path.lexists, rec.tree.root):
                        remove = (
                            workspace.cleanup_merged_worktree
                            if rec.merged
                            else workspace.discard_worktree
                        )
                        await remove(agent, delete_branch=False)
                    else:
                        await self._git(main, "worktree", "prune")
                if await self._yes_no(
                    main, "rev-parse", "--verify", "--quiet", f"refs/heads/{agent}"
                ):
                    # -d after a merge, so a branch that is not in main is never deleted.
                    await self._git(main, "branch", "-d" if rec.merged else "-D", agent)
        except Exception as error:
            await self._error(
                "git",
                f"Cleaning up after {rec.change_id} failed: {error}. "
                "The next decision or agent start tries again.",
                rec,
                fatal=False,
            )
            return
        rec.cleaned = True
