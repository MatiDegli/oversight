"""The change manager: one record per agent's change, from its worktree to a merge or a discard.

A change is `working` until its run ends, then `ready` (proposed) or `unproposed` (no proposal,
such as after a halt whose worktree root was lost). Approve takes `ready`; Reject takes `ready`,
`conflict`, `unproposed` or `broken`. Both enter `deciding`, which always ends: in `closing` once
the decision is a fact, in `conflict`, back in `ready` when main is untouched, or in `broken` when
main's state cannot be read. `closing` becomes `closed` once its resolution, delta and `done` are
all sent; until then the change is pending, keeps its worktree until the resolution is out, and the
next decision or `open` sends the rest. Request changes takes `ready` or `conflict` back to
`working` and discards nothing; from `conflict` it first merges main into the agent's branch, so
the agent redoes its change on the new base. In `ready`, the person's save in review commits to the
agent's worktree and re-proposes; Approve merges only the revision it was clicked on, with the
worktree at that revision's head. Stop takes what Reject takes, or a `working` change, whose run
it cancels and waits for, then discards it as Reject does. Every git call is `Workspace.git`, async
with a timeout; decisions hold main's lock, then the agent's, throughout. Once a change enters
`broken`, `main_suspect` stays true until the process restarts; `check_main` reads main's state,
and `aclose` drains the close-outs before the store closes.

The person's edits on main (docs/plans/files.md): `save_main` commits one file as the user and
`rescan` re-reads main, each one `_whole` step under main's lock, each publishing one
`graph.delta`; `read_file` reads main under that lock, or an agent's worktree under its own.
"""

import asyncio
import functools
import logging
import os
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any, Literal

from oversight.agents import AgentTask, Publish, RunEnd
from oversight.changes._diff import count_lines, file_status, parse_name_status
from oversight.changes._versions import (
    main_delta,
    nodes_in_path,
    parse,
    rescan_delta,
    with_positions,
)
from oversight.events import Clock, EventPayload, system_clock
from oversight.graph import (
    CodeVersion,
    DeltaCause,
    WorkspaceGraph,
    change_cone,
    change_graph,
)
from oversight.workspace import (
    FileRead,
    InvalidPath,
    ProcessResult,
    StaleFile,
    Tree,
    Workspace,
    WorkspaceError,
)
from oversight_contracts.commands import FileContent, FileSaved
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

_log = logging.getLogger(__name__)

type State = Literal[
    "working", "ready", "unproposed", "deciding", "conflict", "closing", "closed", "broken"
]
type Source = Literal["git", "tests", "graph", "server"]
type By = Literal["agent", "user"]
"""Who wrote a file version that a `change.updated` shows."""

APPROVABLE: frozenset[State] = frozenset({"ready"})
REJECTABLE: frozenset[State] = frozenset({"ready", "conflict", "unproposed", "broken"})
"""`broken`: the merge failed and main's state is unknown; Reject still closes the change."""
REVISABLE: frozenset[State] = frozenset({"ready", "conflict"})
STOPPABLE: frozenset[State] = frozenset({*REJECTABLE, "working"})
"""`working`: the run is cancelled first, and the change's outcome waits for its end."""
STOPPED = "Stopped by the user."
SUSPECT = "Main's state is unknown; reject the change and restart the server"
CLOSE_OUT_ATTEMPTS = 2
"""Tries per event on each occasion (a decision, a retry before the next decision or `open`, the
drain)."""
EVENT_TYPES = {
    "requested": "change.resolved changes_requested",
    "stopped": "agent.status stopped",
    "resolved": "change.resolved",
    "delta": "graph.delta",
    "done": "agent.status done",
}
"""Close-out events by their names in `_Change.sent`, as an `error` names them."""


class ChangeNotFound(LookupError):
    """No change has that id: the route answers not_found."""


class WrongState(Exception):
    """The change cannot take that decision now: the route answers wrong_state."""


@dataclass(frozen=True)
class Revision:
    """What the agent resumes with after Request changes."""

    comment: str
    note: str | None
    """Set when the base moved: main merged in, and the files with conflict markers or the tests
    that failed with main."""


@dataclass
class _Move:
    """A base move's facts as read so far, for its recovery."""

    main_head: str | None = None
    old_head: str | None = None


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
    """The head of the last revision the store took."""
    proposed_now: bool = False
    """The current run (the first, or a revision's) proposed; `run_ended` reads it."""
    summary: str | None = None
    """The agent's note on the last revision the store took."""
    steps: int = 0
    requests: int = 0
    """Request changes taken so far; each grows the step budget by `limits.max_steps`."""
    requested: ChangeResolved | None = None
    """A `changes_requested` the store has not taken yet: sent before the next proposal, or
    before a Reject's close-out."""
    conflict: Conflict | None = None
    """The last approval's conflict, for the note of a redo on the new base."""
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
    `broken` once the merge may have touched main, or `conflict` once the approval's conflict is
    sent. The change stays `deciding` until then, so nothing is decided while it is settled."""
    decision: int = 0
    """Counts the decisions taken; only the latest one's task may end `deciding`."""
    taken: int = 0
    """The revision Approve was clicked on (R1): a later one is reviewed before any merge."""
    stopped: bool = False
    """The decision is a Stop: its close-out sends `stopped` before the resolution, no `done`."""

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
        self._tasks: set[asyncio.Task[Any]] = set()
        self._suspect = False

    @property
    def main_suspect(self) -> bool:
        """True once a change has entered `broken`, so main's state is unknown, until the process
        restarts and the next start's `reset` rebuilds main. Rejecting that change keeps it true."""
        return self._suspect

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
    ) -> bool:
        """Publish an `error`; False when the store refused it: nowhere left to say so."""
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
            return False
        return True

    def _spawn[T](self, work: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _whole[T](self, work: Coroutine[Any, Any, T]) -> T:
        """Run `work` as a tracked task and wait for its end through every cancel, then re-raise
        the first cancel. For a publish and the bookkeeping that must match what the store took, so
        a cancel never leaves one without the other; and for a save or rescan on main, whole."""
        task = self._spawn(work)
        cancelled = False
        while not task.done():
            try:
                await asyncio.wait([task])
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
        return task.result()

    async def _scan(self, commit: str) -> GraphSnapshot:
        """M2: `WorkspaceGraph.scan` in a thread. A cancel waits for the thread to return before it
        is re-raised, so no lock is released while the graph is being rewritten."""
        scanning = asyncio.ensure_future(asyncio.to_thread(self._main_graph.scan, commit))
        cancelled = False
        while not scanning.done():
            try:
                await asyncio.wait([scanning])
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            if not scanning.cancelled() and scanning.exception() is not None:
                _log.error("A cancelled scan of main failed: %s", scanning.exception())
            raise asyncio.CancelledError
        return scanning.result()

    # --- Opening a change ---------------------------------------------------------------------

    async def open(self, task: AgentTask, model_id: str) -> Tree:
        """Create the agent's worktree from main's HEAD and record its base files and graph."""
        if task.change_id in self._changes or task.agent_id in self._agents:
            raise WrongState(f"{task.agent_id} or {task.change_id} was already opened")
        await self._finish_pending()
        async with self._workspace.locks(main=True):
            # M5: the route's check is a fast path; main may have turned suspect while this queued.
            if self._suspect:
                raise WrongState(f"{SUSPECT}; nothing was started")
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

    async def _read_files(self, tree: Tree) -> dict[str, str]:
        files: dict[str, str] = {}
        for path in await tree.list_files():
            try:
                files[path] = (await tree.read_text(path)).content
            except WorkspaceError:
                continue  # not UTF-8 text: no agent tool can edit it
        return files

    async def _read_base(self, tree: Tree, commit: str) -> tuple[dict[str, str], CodeVersion]:
        files = await self._read_files(tree)
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

    async def _send_file(self, rec: _Change, path: str, author: By = "agent") -> None:
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
                if rec.requested is not None:
                    # R2: revision n + 1 never precedes its request in the log.
                    failure = await self._send_requested(rec)
                    if failure:
                        raise RuntimeError(f"its request for changes is still unsent ({failure})")
                head_commit, files = await self._commit(rec)
                await self._proposal(rec, head_commit, files, "agent", summary)
            return True
        except Exception as error:
            source: Source = "git" if isinstance(error, WorkspaceError) else "server"
            change = rec.change_id if rec else agent_id
            await self._error(source, f"{change} could not be proposed: {error}", rec, fatal=True)
            return False

    async def _proposal(
        self,
        rec: _Change,
        head: str,
        files: dict[str, FileStatus],
        author: By,
        summary: str | None,
    ) -> None:
        """Republish what differs from what the UI holds, test the worktree, take the cone on the
        agent's version, then publish `change.proposed` (revision n + 1) and record it in one
        `_whole` step. The caller holds the agent's lock and has committed `head`."""
        paths = set(files) | rec.touched | rec.unpublished
        await self._refresh(rec, paths)
        for path in sorted(paths):
            modified = rec.head_files.get(path)
            fresh = path in rec.published and rec.published[path] == modified
            if path in rec.unpublished or not fresh:
                await self._send_file(rec, path, author)
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
            for plus, minus in [count_lines(rec.base_files.get(path), rec.head_files.get(path))]
        ]
        proposal = ChangeProposed(
            change_id=rec.change_id,
            agent_id=rec.agent_id,
            revision=rec.revision + 1,
            task=rec.task.task,
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
        await self._whole(self._record_proposal(rec, proposal))

    async def _record_proposal(self, rec: _Change, proposal: ChangeProposed) -> None:
        """R7: a run cancelled during this publish still finds the record matching the store."""
        await self._publish(proposal)
        rec.revision, rec.head_commit = proposal.revision, proposal.head_commit
        rec.proposed_now, rec.summary = True, proposal.summary

    async def _commit(self, rec: _Change, by: By = "agent") -> tuple[str, dict[str, FileStatus]]:
        """Commit the worktree as the agent, or as the user for edits in review; HEAD, and the
        files that differ from the base."""
        tree = rec.tree
        await self._git(tree, "add", "-A")
        if (await self._git(tree, "status", "--porcelain")).strip():
            if by == "agent":
                message, author = f"{rec.change_id} ({rec.agent_id})", rec.agent_id
            else:
                message, author = f"{rec.change_id}: edited in review", "user"
            await self._git(tree, "commit", "-q", "-m", message, commit_as=author)
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

    def run_ended(self, agent_id: AgentId, end: RunEnd | None, *, steps: int | None = None) -> None:
        """The run is over (`end` is `None` when it raised or was cancelled): `ready` if this run
        proposed, else `unproposed`. `steps`, the run's own count, wins over `end.steps`. A run
        that a Stop cancelled finds its change `deciding`: that outcome becomes the Stop's
        `fallback`, where a Stop that ends without settling leaves the change."""
        rec = self._agents.get(agent_id)
        if rec is None:
            raise ChangeNotFound(agent_id)
        stopping = rec.state == "deciding" and rec.fallback == "working"
        if rec.state != "working" and not stopping:
            return
        if steps is not None:
            rec.steps = steps
        elif end is not None:
            rec.steps = end.steps
        outcome: State = "ready" if rec.proposed_now else "unproposed"
        if stopping:
            rec.fallback = outcome
        else:
            rec.state = outcome

    def _unreported(self, rec: _Change) -> None:
        """Settle a run that will never report its end, as `run_ended` does for one that did: none
        was passed to a Stop, or the one passed was cancelled before its first step and so never
        ran its `finally`. Without it a Stop cancelled before its body, or in its wait for the run,
        leaves the change `working` with no run. A no-op once a run has reported."""
        self.run_ended(rec.agent_id, None)

    # --- The person's edits in review ---------------------------------------------------------

    async def save_for_review(
        self, agent_id: AgentId, path: str, content: str, base_hash: str
    ) -> str:
        """The person's save on the new side in review: write `path` in the agent's worktree,
        commit it as the user and re-propose (R6), so Approve never merges an untested edit; the
        file's new hash. Only from `ready`, checked again under the agent's lock, which the save
        holds throughout (`WrongState`); `StaleFile` when the file is not the one `base_hash` names.
        R9: with nothing to commit and HEAD still the last proposal's, nothing is sent. A save cut
        short after its write is re-proposed by the next Approve (R1)."""
        rec = self._agents.get(agent_id)
        if rec is None:
            raise ChangeNotFound(agent_id)
        self._check_editable(rec)
        async with self._workspace.locks(agent_id=agent_id):
            self._check_editable(rec)  # a decision may have taken it while the save waited
            saved = await rec.tree.write_text(path, content, expected_hash=base_hash)
            rec.touched.add(path)
            head, files = await self._commit(rec, "user")
            if head != rec.head_commit:
                await self._proposal(rec, head, files, "user", rec.summary)
        return saved

    @staticmethod
    def _check_editable(rec: _Change) -> None:
        if rec.state != "ready":
            raise WrongState(f"{rec.change_id} is {rec.state}; a save in review needs ready")

    async def _as_clicked(self, rec: _Change) -> bool:
        """R1: merge only the revision Approve was clicked on, with the worktree exactly at its
        head. A save proposed after the click is left for review; one cut short, whose write or
        commit landed without its proposal, is re-proposed from what the worktree holds, leftovers
        committed as the user. Either way an `error` names the revision to review and nothing
        merges: main is untouched, so the decision's end puts the change back to `ready`. The
        caller holds both locks."""
        if rec.revision != rec.taken:
            await self._error(
                "server",
                f"Revision {rec.revision} of {rec.change_id} arrived after you clicked; "
                "review it and approve again.",
                rec,
                fatal=False,
            )
            return False
        tree = rec.tree
        try:
            head = (await self._git(tree, "rev-parse", "HEAD")).strip()
            if (
                head == rec.head_commit
                and not (await self._git(tree, "status", "--porcelain")).strip()
            ):
                return True
            head, files = await self._commit(rec, "user")
            await self._proposal(rec, head, files, "user", rec.summary)
        except Exception as error:
            source: Source = "git" if isinstance(error, WorkspaceError) else "server"
            await self._error(
                source,
                f"Approving {rec.change_id} merged nothing: its worktree could not be checked or "
                f"re-proposed ({error}); approve again or reject.",
                rec,
                fatal=False,
            )
            return False
        await self._error(
            "server",
            f"Revision {rec.revision} of {rec.change_id} holds edits that were not proposed when "
            "you clicked; review it and approve again.",
            rec,
            fatal=False,
        )
        return False

    # --- The person's edits on main -----------------------------------------------------------

    async def read_file(self, agent_id: AgentId | None, path: str) -> FileContent:
        """GET /files. Main is read under main's lock, which every mutation of main holds from its
        first git call to its last publish, so a read sees a whole commit, never the tree of a merge
        waiting for its tests, nor conflict markers before the abort; a suspect main is read as it
        stands. An agent's worktree is read under its own lock, in any state until its cleanup.
        `ChangeNotFound` for no live worktree, `FileNotFoundError` for a missing file, `InvalidPath`
        for a path the workspace refuses and for a file that is not UTF-8 text."""
        if agent_id is None:
            async with self._workspace.locks(main=True):
                read = await self._read_text(self._workspace.main, path)
        else:
            rec = self._agents.get(agent_id)
            if rec is None:
                raise ChangeNotFound(agent_id)
            async with self._workspace.locks(agent_id=agent_id):
                if rec.cleaned:  # read under the lock a cleanup holds
                    raise ChangeNotFound(agent_id)
                read = await self._read_text(rec.tree, path)
        return FileContent(path=path, content=read.content, hash=read.hash, agent_id=agent_id)

    @staticmethod
    async def _read_text(tree: Tree, path: str) -> FileRead:
        """`tree.read_text`, with what a person's request can meet as `InvalidPath`: a path through
        a file, and a file that is not UTF-8 text (a directory is refused by the workspace)."""
        try:
            return await tree.read_text(path)
        except NotADirectoryError as error:
            raise InvalidPath(f"invalid path {path[:80]!r}: a parent is not a directory") from error
        except WorkspaceError as error:
            if isinstance(error.__cause__, UnicodeDecodeError):
                raise InvalidPath(f"{path[:80]!r} is not a UTF-8 text file") from error
            raise

    async def save_main(self, path: str, content: str, base_hash: str) -> FileSaved:
        """PUT /files on main: commit `content` for `path` as the user, a commit holding that file
        only, and publish one `graph.delta` (`user_edit`). One `_whole` step: a cancel of the
        request waits for the save to end, so none falls between the write, the commit and the
        publish. `StaleFile` when the file is missing or is not the one `base_hash` names (a save
        never creates a file), `InvalidPath`, `WrongState` for a suspect, dirty or mid-merge main.
        The same bytes commit nothing. A failed commit restores the file and raises
        `WorkspaceError`; the commit is the save, so a failed rescan after it still answers saved.
        Runs no tests: the next approval tests the combination (R5)."""
        return await self._whole(self._save_main(path, content, base_hash))

    async def _save_main(self, path: str, content: str, base_hash: str) -> FileSaved:
        # M4: a pending close-out usually sends its delta here, moving `snapshot`, before the
        # save's delta is built from it.
        await self._finish_pending()
        main = self._workspace.main
        async with self._workspace.locks(main=True):
            await self._drain_pending()
            blocked = await self._main_blocked()
            if blocked is not None:
                raise WrongState(f"{blocked}; nothing was saved")
            pre = (await self._git(main, "rev-parse", "HEAD")).strip()
            try:
                original = (await self._read_text(main, path)).content
            except FileNotFoundError:
                raise StaleFile(f"{path} no longer exists") from None
            saved = await main.write_text(path, content, expected_hash=base_hash)
            if saved == base_hash:
                return FileSaved(path=path, hash=saved)  # the same bytes: no commit, no event
            try:
                # `:(literal)`: the path is a file's name, never a glob or a pathspec's magic.
                await self._git(main, "add", "--", f":(literal){path}")
                await self._git(main, "commit", "-q", "-m", f"Edit {path}", commit_as="user")
            except Exception as failure:
                await self._settle_save(path, pre, original, saved, failure)
            try:
                head = (await self._git(main, "rev-parse", "HEAD")).strip()
                await self._publish_main(head, "user_edit")
            except Exception as error:
                # The snapshot stays: the next merge, save or rescan covers this commit.
                message = f"Main's graph was not updated after saving {path}: {error}"
                await self._report("graph", message, fatal=False)
            return FileSaved(path=path, hash=saved)

    async def _settle_save(
        self, path: str, pre: str, original: str, saved: str, failure: Exception
    ) -> None:
        """B2: the save's `add` or `commit` failed, or timed out after it ran. Decide from git's
        facts first, then act. A commit that landed whole returns, and the save goes on. Main
        still at `pre`: put `original` back and stage it, so the index matches HEAD; confirmed
        clean, main stays trusted. Anything else, an unreadable fact or a restore that fails: main
        is suspect until restart. Either way raises `WorkspaceError` after its `error` event."""
        landed = await self._save_landed(path, pre, saved)
        if landed:
            return
        if landed is False and await self._restore_save(path, original, saved):
            await self._report(
                "git", f"Saving {path} failed ({failure}); main is unchanged.", fatal=False
            )
        else:
            self._suspect = True
            await self._report(
                "git",
                f"Saving {path} failed ({failure}) and main's state is unknown; "
                "reject the change and restart the server.",
                fatal=True,
            )
        raise WorkspaceError(f"saving {path} failed")

    async def _save_landed(self, path: str, pre: str, saved: str) -> bool | None:
        """True when the commit landed whole: HEAD is a child of `pre` that changed `path` alone,
        main is clean and the file on disk is the saved one. False when main is at `pre` with no
        merge. None for anything else, and when a fact cannot be read."""
        main = self._workspace.main
        try:
            head = (await self._git(main, "rev-parse", "HEAD")).strip()
            if await self._yes_no(main, "rev-parse", "-q", "--verify", "MERGE_HEAD"):
                return None
            if head == pre:
                return False
            parent = (await self._git(main, "rev-parse", "HEAD^")).strip()
            diff = ("diff", "--name-only", "--no-renames", "-z", pre, "HEAD")
            names = [name for name in (await self._git(main, *diff)).split("\0") if name]
            clean = not (await self._git(main, "status", "--porcelain")).strip()
            on_disk = (await main.read_text(path)).hash
        except Exception:
            return None
        return True if parent == pre and names == [path] and clean and on_disk == saved else None

    async def _restore_save(self, path: str, original: str, saved: str) -> bool:
        """Write `original` back over the saved file and stage it; True once status is clean."""
        main = self._workspace.main
        try:
            await main.write_text(path, original, expected_hash=saved)
            await self._git(main, "add", "--", f":(literal){path}")
            return not (await self._git(main, "status", "--porcelain")).strip()
        except Exception:
            return False

    async def rescan(self) -> None:
        """POST /graph/rescan: read main again and publish one delta (`rescan`) if anything moved:
        a commit made behind the server's back, or one whose scan raised. One `_whole` step. Main
        that is suspect, mid-merge or dirty is not main's graph at any commit: refused with an
        `error` event and nothing published, until the contract lists `wrong_state` for it (P1).
        A scan or publish that raises sends an `error`, keeps `snapshot` and re-raises."""
        await self._whole(self._rescan())

    async def _rescan(self) -> None:
        await self._finish_pending()
        async with self._workspace.locks(main=True):
            await self._drain_pending()
            blocked = await self._main_blocked()
            if blocked is not None:
                await self._report("graph", f"{blocked}; nothing was rescanned.", fatal=False)
                return
            try:
                head = (await self._git(self._workspace.main, "rev-parse", "HEAD")).strip()
                await self._publish_main(head, "rescan")
            except Exception as error:
                await self._report("graph", f"Main's graph was not rescanned: {error}", fatal=False)
                raise

    async def _main_blocked(self) -> str | None:
        """Why main cannot take a commit or a rescan now, or None: M5's check, then `check_main`'s
        facts. The caller holds main's lock, and `check_main` takes it again."""
        if self._suspect:
            return SUSPECT
        clean = await self.check_main()
        if clean is None:
            return "Main's state could not be read"
        return None if clean else "Main has uncommitted edits or a merge in progress"

    async def _publish_main(self, head: str, cause: DeltaCause) -> None:
        """M3: scan main at `head` and send the one delta from the graph the UI holds, then move
        `snapshot` to it with no await between. A rescan sends none when nothing moved."""
        new = await self._scan(head)
        old = self.snapshot
        delta = (
            rescan_delta(old, new, head) if cause == "rescan" else main_delta(old, new, head, cause)
        )
        if delta is None:
            return
        self._supersede_pending()
        await self._publish(delta)
        self.snapshot = with_positions(new, old)

    async def _report(self, source: Source, message: str, *, fatal: bool) -> None:
        """An `error` with no change or agent; a store that refuses it leaves a log line."""
        if not await self._error(source, message, None, fatal=fatal):
            _log.error("The store refused an error event: %s", self._workspace.redact(message))

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

    def _decide[T](self, rec: _Change, work: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        """Run a decision in a tracked task. A decision that ends still `deciding`, such as a task
        cancelled before its first step, which runs none of its code, goes back to `fallback`
        through a done-callback, which runs before anyone awaiting the task resumes. The callback
        keeps its decision's number: a decision that settled, then took a while to report it, ends
        after the next one was taken, and must leave that one `deciding`."""
        task = self._spawn(work)
        decision = rec.decision
        task.add_done_callback(lambda _: self._restore(rec, decision))
        return task

    def _restore(self, rec: _Change, decision: int) -> None:
        if rec.state == "deciding" and rec.decision == decision:
            if rec.fallback == "broken":
                self._break(rec)
            else:
                rec.state = rec.fallback

    def _break(self, rec: _Change) -> None:
        """Main's state is unknown: the change is `broken`, and main suspect until restart."""
        rec.state = "broken"
        self._suspect = True

    def approve(self, change_id: ChangeId, comment: str | None) -> asyncio.Task[None]:
        rec = self._take(change_id, APPROVABLE, "approve")
        rec.taken = rec.revision
        return self._decide(rec, self._approve(rec, comment))

    def reject(self, change_id: ChangeId, comment: str | None) -> asyncio.Task[None]:
        rec = self._take(change_id, REJECTABLE, "reject")
        return self._decide(rec, self._reject(rec, comment))

    def request_changes(self, change_id: ChangeId, comment: str) -> asyncio.Task[Revision | None]:
        """Send the comment back to the agent; its worktree, branch, base and files stay. From
        `conflict`, main is merged into the agent's branch first. The task returns what the agent
        resumes with, the change `working`; or `None` after an `error` when the base could not
        move, the change still `conflict` (or `broken`, when the worktree's state is unknown), or
        when main turned suspect while the request queued, the change still as it was taken."""
        rec = self._take(change_id, REVISABLE, "request changes")
        return self._decide(rec, self._request_changes(rec, comment))

    def stop(self, agent_id: AgentId, run: asyncio.Task[None] | None) -> asyncio.Task[None]:
        """Stop the agent and discard its change: from what Reject takes, or from `working`, whose
        live `run` is cancelled in this same call. The task waits for the run's end, so the
        resolution names the revision the store took (R7), then closes out as Reject does, with
        `reason="stopped"`, `agent.status` `stopped` before the resolution, and no `done`."""
        rec = self._agents.get(agent_id)
        if rec is None:
            raise ChangeNotFound(agent_id)
        self._take(rec.change_id, STOPPABLE, "stop")
        if run is None:
            self._unreported(rec)
        else:
            run.cancel()
            # Registered here, not in the Stop's task, which a cancel can end before its body or in
            # its wait for the run. Before the wait's own callback, so it settles first.
            run.add_done_callback(lambda _: self._unreported(rec))
        return self._decide(rec, self._stop(rec, run))

    async def check_main(self) -> bool | None:
        """Under main's lock: True when main has no `MERGE_HEAD` and `status --porcelain` is empty,
        False otherwise, None when git fails or times out."""
        main = self._workspace.main
        async with self._workspace.locks(main=True):
            try:
                merging = await self._yes_no(main, "rev-parse", "-q", "--verify", "MERGE_HEAD")
                dirty = bool((await self._git(main, "status", "--porcelain")).strip())
            except (WorkspaceError, OSError):
                return None
        return not merging and not dirty

    async def aclose(self) -> None:
        """Drain before the store closes, which the caller does only once this returns: wait for
        the decisions in flight, run the close-out retry once more for each change with unsent
        events, then send one `error` per change naming each event still unsent. If the store
        refuses that `error` too, one log line per change names what was lost. Cleanup is left
        to the next start's `reset`."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        for rec in list(self._pending.values()):
            async with self._workspace.locks(main=True, agent_id=rec.agent_id):
                if rec.state == "closing":
                    failure = await self._try_close_out(rec)
                    names = [name for name, _ in self._unsent(rec)]
                elif rec.requested is not None:
                    failure, names = await self._send_requested(rec), ["requested"]
                else:
                    continue  # all sent; only its cleanup is pending
                if failure:
                    unsent = ", ".join(EVENT_TYPES[name] for name in names)
                    reported = await self._error(
                        "server",
                        f"Shutting down with the close-out of {rec.change_id} incomplete "
                        f"({failure}): {unsent} never sent.",
                        rec,
                        fatal=False,
                    )
                    if not reported:
                        # Only the event names and ids: no payload, no store message.
                        _log.error(
                            "Shutting down with the close-out of %s (agent %s) incomplete; "
                            "never sent, and the store refused the error that names them: %s",
                            rec.change_id,
                            rec.agent_id,
                            unsent,
                        )

    async def _approve(self, rec: _Change, comment: str | None) -> None:
        await self._finish_pending()
        async with self._workspace.locks(main=True, agent_id=rec.agent_id):
            # A close-out publish left running by a cancel may still move `snapshot`, which this
            # merge's delta starts from, and must find its own delta still there: wait for it.
            await self._drain_pending()
            if self._suspect:
                # M5: main turned suspect while this queued. No git call; the decision's end puts
                # the change back to `ready`.
                await self._error(
                    "server",
                    "Main became suspect before this approval ran; reject the change or restart.",
                    rec,
                    fatal=False,
                )
                return
            if not await self._as_clicked(rec):
                return
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

    async def _request_changes(self, rec: _Change, comment: str) -> Revision | None:
        await self._finish_pending()
        note: str | None = None
        if rec.fallback == "conflict":
            # Phase A, cancellable: a cancel before phase B leaves the change as it was.
            async with self._workspace.locks(main=True, agent_id=rec.agent_id):
                if await self._refused_as_suspect(rec):
                    return None
                move = _Move()
                try:
                    note = await self._move_base(rec, move)
                except asyncio.CancelledError:
                    await self._settle_move(rec, move, None)
                    raise
                except Exception as error:
                    await self._settle_move(rec, move, error)
                    return None
            return await self._whole(self._requested(rec, comment, note))
        # From `ready`, under both locks as every decision: a save in review holds the agent's
        # lock until its revision is proposed, so the request names that revision and precedes
        # the revision run.
        async with self._workspace.locks(main=True, agent_id=rec.agent_id):
            if await self._refused_as_suspect(rec):
                return None
            return await self._whole(self._requested(rec, comment, note))

    async def _refused_as_suspect(self, rec: _Change) -> bool:
        """F1, under main's lock: main turned suspect while the request queued, so it is refused
        with one `error`, no base move, no `changes_requested` and no revision; the decision's end
        puts the change back to `ready` or `conflict`, where it was taken from."""
        if not self._suspect:
            return False
        await self._error(
            "server",
            "Main became suspect before this request ran; reject it or restart.",
            rec,
            fatal=False,
        )
        return True

    async def _move_base(self, rec: _Change, move: _Move) -> str:
        """Merge main's HEAD into the agent's branch, committing as the agent; unmerged paths are
        committed with their markers, for the agent, which has no git, to resolve. The note."""
        main, tree = self._workspace.main, rec.tree
        move.main_head = (await self._git(main, "rev-parse", "HEAD")).strip()
        if (await self._git(main, "status", "--porcelain")).strip():
            raise WorkspaceError("main has uncommitted changes")
        move.old_head = (await self._git(tree, "rev-parse", "HEAD")).strip()
        message = f"Merge main into {rec.change_id} ({rec.agent_id})"
        result = await self._run(tree, "merge", "--no-ff", "--no-commit", move.main_head)
        if result.returncode == 0:
            # Exit 0 with no MERGE_HEAD: the branch already holds main.
            if await self._yes_no(tree, "rev-parse", "-q", "--verify", "MERGE_HEAD"):
                await self._git(tree, "commit", "-q", "-m", message, commit_as=rec.agent_id)
        elif result.returncode == 1 and (
            await self._git(tree, "diff", "--name-only", "--diff-filter=U", "-z")
        ).strip("\0"):
            await self._git(tree, "add", "-A")
            await self._git(tree, "commit", "-q", "-m", message, commit_as=rec.agent_id)
        else:
            detail = "timed out" if result.timed_out else self._workspace.redact(result.stderr)
            raise WorkspaceError(f"git merge failed: {detail.strip()[-500:]}")
        await self._moved(rec, move.main_head)
        return self._move_note(rec)

    async def _moved(self, rec: _Change, main_head: str) -> None:
        """R4: the base is `main_head`. Both versions come from the trees, the head from every file
        of the worktree, never only the paths in the new diff, so what main added stays in the
        graph and the cone. Each file whose original or modified changed is shown again. Nothing
        is recorded until every read is done; a file not shown stays in `unpublished`, which the
        next edit or proposal sends."""
        main, tree = self._workspace.main, rec.tree
        base_files, base = self._bases.get(main_head) or await self._read_base(main, main_head)
        head_files = await self._read_files(tree)
        head = await parse(head_files, main_head, None)
        diff = ("diff", "--name-only", "--no-renames", "-z", main_head, "HEAD")
        names = await self._git(tree, *diff)
        touched = {path for path in names.split("\0") if path}
        for path in rec.published.keys() | rec.touched | touched:
            before = rec.base_files.get(path), rec.head_files.get(path)
            if before != (base_files.get(path), head_files.get(path)):
                rec.unpublished.add(path)
        rec.base_commit, rec.base_files, rec.base = main_head, base_files, base
        rec.head_files, rec.head, rec.touched = head_files, head, touched
        rec.graph = change_graph(base, head)
        for path in sorted(rec.unpublished):
            try:
                await self._send_file(rec, path)
            except Exception as error:
                await self._error(
                    "server", f"{path} was not shown again: {error}", rec, fatal=False
                )

    @staticmethod
    def _move_note(rec: _Change) -> str:
        markers = [
            path
            for path in sorted(rec.touched)
            if any(line.startswith("<<<<<<< ") for line in rec.head_files.get(path, "").split("\n"))
        ]
        note = (
            "Main was merged into your branch: your change now starts from main's latest version."
        )
        if markers:
            note += f" These files have git conflict markers to resolve: {', '.join(markers)}."
        tests = rec.conflict.tests if rec.conflict is not None else None
        if tests is not None:
            failed = ", ".join(tests.failed_tests) or tests.outcome
            note += f" The tests failed with your change on main: {failed}."
        return note

    async def _settle_move(self, rec: _Change, move: _Move, error: Exception | None) -> None:
        """R3: a base move failed or was cancelled. Still under both locks, decide from the
        worktree's facts: abort an unfinished merge; clean at the old head is unchanged; clean at a
        merge of the old head and main's is a move that landed, recorded from those facts; anything
        else, git failing, or a second cancel cutting this recovery, is `broken`. Main was only
        read, so `main_suspect` stays as it was. The change ends `conflict` or `broken`, with no
        resolution sent; the second cancel is re-raised once the `error` is out."""
        tree = rec.tree
        cut = False
        try:
            if await self._yes_no(tree, "rev-parse", "-q", "--verify", "MERGE_HEAD"):
                await self._git(tree, "merge", "--abort")
            head = (await self._git(tree, "rev-parse", "HEAD")).strip()
            clean = not (await self._git(tree, "status", "--porcelain")).strip()
            parents = (await self._git(tree, "rev-list", "--parents", "-n", "1", head)).split()[1:]
            if clean and move.old_head in (None, head):  # None: the merge never ran
                note = "its worktree is unchanged; request changes again or reject"
            elif clean and move.main_head and parents == [move.old_head, move.main_head]:
                await self._moved(rec, move.main_head)
                note = "main was merged into its worktree; request changes again to revise"
            else:
                raise WorkspaceError("the worktree is neither unchanged nor merged")
            rec.state = "conflict"
        except (Exception, asyncio.CancelledError) as failure:
            # A second cancel may have cut the recovery before its abort ran, so the worktree can
            # still be mid-merge; `_restore` would put such a change back to `conflict`.
            cut = isinstance(failure, asyncio.CancelledError)
            rec.state = "broken"
            note = "its worktree's state is unknown; reject the change"
        what = "was cancelled" if error is None else f"failed: {error}"
        await self._error(
            "git", f"Requesting changes on {rec.change_id} {what}; {note}.", rec, fatal=False
        )
        if cut:
            raise asyncio.CancelledError

    async def _requested(self, rec: _Change, comment: str, note: str | None) -> Revision:
        """Phase B, one `_whole` step: the resolution and `working` go together whatever cancels
        come. R2: a resolution the store refused twice stays pending, and the revision starts."""
        rec.requested = ChangeResolved(
            change_id=rec.change_id,
            agent_id=rec.agent_id,
            revision=rec.revision,
            decision="changes_requested",
            reason=None,
            comment=comment,
            merge=None,
            merge_commit=None,
            conflict=None,
        )
        failure = await self._send_requested(rec)
        if failure:
            self._pending[rec.agent_id] = rec
            await self._error(
                "server",
                f"The request for changes on {rec.change_id} was not sent ({failure}); "
                "it is sent before the next proposal.",
                rec,
                fatal=False,
            )
        rec.state, rec.proposed_now = "working", False
        rec.requests += 1
        return Revision(comment, note)

    async def _send_requested(self, rec: _Change) -> str:
        """Send a pending `changes_requested`, up to `CLOSE_OUT_ATTEMPTS` tries: the last failure,
        or "" once the store took it. A change pending only for it then leaves `_pending`."""
        await self._drain(rec)
        failure = ""
        for _ in range(CLOSE_OUT_ATTEMPTS):
            if rec.requested is None:
                break
            failure = await self._send_event(rec, "requested", rec.requested)
        if rec.requested is not None:
            return failure
        if rec.state not in ("closing", "closed"):
            self._pending.pop(rec.agent_id, None)
        return ""

    async def _reject(self, rec: _Change, comment: str | None) -> None:
        await self._finish_pending()
        async with self._workspace.locks(main=True, agent_id=rec.agent_id):
            rec.resolution = self._resolution(rec, "rejected", comment)
            await self._finish(rec)  # main did not change: no rescan and no delta

    async def _stop(self, rec: _Change, run: asyncio.Task[None] | None) -> None:
        if run is not None:
            # Its end calls `run_ended`, which records its steps and sets `fallback`; a run cut in
            # its proposal's publish records the proposal first (R7). A run that never started
            # reports nothing: `stop` settled it with a done-callback, which ran before this wait
            # returned, so `fallback` is ready or unproposed from here on.
            await asyncio.wait([run])
        await self._finish_pending()
        async with self._workspace.locks(main=True, agent_id=rec.agent_id):
            # Under the agent's lock: a save in review proposes first, and this closes its revision.
            rec.resolution = self._resolution(rec, "rejected", None, reason="stopped")
            rec.stopped = True
            await self._finish(rec)

    def _resolution(
        self,
        rec: _Change,
        decision: Literal["approved", "rejected"],
        comment: str | None,
        *,
        merge_commit: str | None = None,
        conflict: Conflict | None = None,
        reason: Literal["stopped"] | None = None,
    ) -> ChangeResolved:
        merge: Literal["merged", "conflict"] | None = None
        if decision == "approved":
            merge = "conflict" if conflict is not None else "merged"
        return ChangeResolved(
            change_id=rec.change_id,
            agent_id=rec.agent_id,
            revision=rec.revision,
            decision=decision,
            reason=reason,
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
            if pre != rec.base_commit and not await self._combined_tests_pass(rec, comment, pre):
                return
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
        await self._conflicted(rec, comment, Conflict(kind="merge", paths=paths, tests=None))
        return True

    async def _combined_tests_pass(self, rec: _Change, comment: str | None, pre: str) -> bool:
        """R5: main moved since the change's base, so the merge, staged on main, is tested before
        its commit. A failure or timeout aborts it and resolves as a tests conflict. A cancel
        during the run is `_recover`'s, which aborts on `MERGE_HEAD`."""
        main = self._workspace.main
        run = await self._workspace.run_tests(main, timeout_s=rec.task.limits.test_timeout_s)
        if run.result.outcome == "passed":
            return True
        await self._git(main, "merge", "--abort")
        if not (await self._facts(rec.head_commit or rec.base_commit)).unchanged(pre):
            raise WorkspaceError("main did not return to its HEAD after the combined tests")
        await self._conflicted(rec, comment, Conflict(kind="tests", paths=[], tests=run.result))
        return False

    async def _conflicted(self, rec: _Change, comment: str | None, conflict: Conflict) -> None:
        """Main is back at `pre`: send the approval's conflict and record it, as one `_whole` step,
        so a cancel never leaves a resolution in the store that the record, and so a second
        Approve or a Request changes, does not know. The change stays `deciding`, with `conflict`
        as its fallback: the decision's end, or its recovery, makes it `conflict`, and no other
        decision enters before."""
        resolution = self._resolution(rec, "approved", comment, conflict=conflict)
        await self._whole(self._record_conflict(rec, resolution, conflict))

    async def _record_conflict(
        self, rec: _Change, resolution: ChangeResolved, conflict: Conflict
    ) -> None:
        await self._publish(resolution)
        rec.conflict, rec.fallback = conflict, "conflict"

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
        abort an unfinished merge, record a landed one; a conflict already sent stays `conflict`. No
        `reset --hard`, no lock removal. A cancelled approval always reports; a landed merge closes
        out first and leaves its cleanup to the next decision or `open`."""
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
            if rec.fallback == "conflict":  # set only by `_record_conflict`: the store has it
                rec.state = "conflict"
                note = "main is unchanged and the conflict is recorded; request changes or reject"
            else:
                rec.state = "ready"
                note = "main is unchanged; approve again or reject"
        else:
            self._break(rec)
            note = "main's state is unknown; reject the change or restart"
        what = "was cancelled" if error is None else f"failed: {error}"
        await self._error("git", f"Approving {rec.change_id} {what}. {note}.", rec, fatal=False)

    async def _record_merge(self, rec: _Change, comment: str | None, post: str) -> None:
        """The merge is a fact: rescan main and build the resolution and delta, sending nothing."""
        try:
            new = await self._scan(post)
        except Exception as error:
            # The snapshot stays, so the next delta starts from what the UI holds.
            await self._error("graph", f"Main's graph was not rescanned: {error}", rec, fatal=False)
        else:
            rec.delta = main_delta(self.snapshot, new, post, "merge")
            rec.next_snapshot = with_positions(new, self.snapshot)
            self._supersede_pending(rec)
        rec.merged = True
        rec.resolution = self._resolution(rec, "approved", comment, merge_commit=post)

    def _status(self, rec: _Change, state: Literal["done", "stopped"]) -> AgentStatus:
        task = rec.task
        return AgentStatus(
            agent_id=task.agent_id,
            slot=task.slot,
            change_id=task.change_id,
            task=task.task,
            model=rec.model_id,
            state=state,
            step=rec.steps,
            max_steps=task.limits.max_steps * (rec.requests + 1),
            detail=STOPPED if state == "stopped" else None,
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
        """Before a decision or an `open`: retry every unfinished close-out and cleanup, and every
        unsent request for changes, which closes nothing."""
        for rec in list(self._pending.values()):
            if rec.state in ("closing", "closed"):
                await self._finish(rec)
            else:
                async with self._workspace.locks(main=True, agent_id=rec.agent_id):
                    await self._send_requested(rec)

    async def _close_out(self, rec: _Change) -> None:
        """The only way into `closed`: the resolution, the delta (approve only), then `done`, each
        marked when sent, so nothing goes twice. Until all are out the change is `closing` and
        pending, and a later call sends only what is missing. Never raises, save a cancel."""
        rec.state = "closing"
        self._pending[rec.agent_id] = rec
        failure = await self._try_close_out(rec)
        if failure:
            await self._error(
                "server",
                f"The close-out of {rec.change_id} is incomplete ({failure}). "
                "The next decision or agent start sends the rest.",
                rec,
                fatal=False,
            )

    async def _try_close_out(self, rec: _Change) -> str:
        """Send what is missing, each event up to `CLOSE_OUT_ATTEMPTS` times; `closed` once all is
        out, else the failure that stopped it. The caller holds main's lock and the change's."""
        failure = await self._send_close_out(rec)
        if not failure:
            rec.state = "closed"
        return failure

    def _unsent(self, rec: _Change) -> list[tuple[str, EventPayload]]:
        """The close-out events not yet sent, in order: an unsent request for changes, the
        resolution, the delta, `done`; for a Stop, the request, `stopped`, then the resolution, so
        the agent's last status precedes the resolution that frees its slot."""
        events: list[tuple[str, EventPayload | None]]
        if rec.stopped:
            events = [
                ("requested", rec.requested),
                ("stopped", self._status(rec, "stopped")),
                ("resolved", rec.resolution),
            ]
        else:
            events = [
                ("requested", rec.requested),
                ("resolved", rec.resolution),
                ("delta", rec.delta),
                ("done", self._status(rec, "done")),
            ]
        return [(n, p) for n, p in events if p is not None and n not in rec.sent]

    async def _send_close_out(self, rec: _Change) -> str:
        """Send what is missing, in order, each event up to `CLOSE_OUT_ATTEMPTS` times; the last
        failure of the first event that never went out, or "" once everything is out. A publish
        that a cancelled close-out left running is waited for first, so it is not resent."""
        await self._drain(rec)
        for name, payload in self._unsent(rec):
            failure = ""
            for _ in range(CLOSE_OUT_ATTEMPTS):
                failure = await self._send_event(rec, name, payload)
                if not failure:
                    break
            if failure:
                return failure  # later events wait: the resolution comes first
        return ""

    async def _send_event(self, rec: _Change, name: str, payload: EventPayload) -> str:
        """One try at one event: its failure, or "" once the store took it."""
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
        return "" if failure is None else f"{name}: {failure}"

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
        if name == "requested":
            rec.requested = None  # one per request, so never in `sent`
            return
        rec.sent.add(name)
        if name == "delta" and rec.next_snapshot is not None:
            self.snapshot = rec.next_snapshot

    async def _drain_pending(self) -> None:
        """Under main's lock, before a delta is built from `snapshot`: wait for every close-out
        publish a cancel left running, which may still move it."""
        for other in list(self._pending.values()):
            await self._drain(other)

    def _supersede_pending(self, built: _Change | None = None) -> None:
        """A delta was built from `snapshot`: it starts from the graph the UI holds and covers
        every merge not yet sent. Sent later, an older one would start from a graph the UI no
        longer holds, so each pending change drops its delta (`built`'s own stays)."""
        for other in self._pending.values():
            if other is not built and "delta" not in other.sent:
                other.delta = other.next_snapshot = None

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
