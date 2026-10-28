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
from collections.abc import Coroutine
from typing import Any

from oversight.agents import AgentTask, Publish, ReviewOutcome, RunEnd
from oversight.changes import _approval as approval
from oversight.changes import _cross_review as cross_review
from oversight.changes import _delivery as delivery
from oversight.changes import _files as files
from oversight.changes import _proposal as proposal
from oversight.changes import _revision as revision
from oversight.changes._decision_validation import validate_decision_target
from oversight.changes._evidence import WriteEvidence
from oversight.changes._resolution_validation import validate_resolution_intent
from oversight.changes._review_port import ReviewCapture, ReviewPort
from oversight.changes._state import (
    APPROVABLE,
    REJECTABLE,
    REVISABLE,
    STOPPABLE,
    ChangeNotFound,
    ChangeRecord,
    ChangeState,
    Revision,
    State,
    WrongState,
)
from oversight.changes._suite_evidence import publish_suite
from oversight.changes._support import ChangeContext, ChangePorts
from oversight.events import Clock, system_clock
from oversight.graph import (
    WorkspaceGraph,
)
from oversight.workspace import (
    SuiteRun,
    Tree,
    Workspace,
)
from oversight_contracts.commands import FileContent, FileSaved
from oversight_contracts.common import AgentId, ChangeId
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.review import FinishDeclaration


class ChangeManager:
    "Implements the runtime's change sink and the decisions. One instance per run."

    def __init__(
        self,
        workspace: Workspace,
        publish: Publish,
        *,
        main_graph: WorkspaceGraph,
        main_snapshot: GraphSnapshot,
        clock: Clock = system_clock,
        git_timeout_s: float = 30,
        review: ReviewPort | None = None,
    ) -> None:
        self._ctx = ChangeContext(
            workspace,
            publish,
            main_graph,
            clock,
            git_timeout_s,
            ChangeState(main_snapshot),
            ChangePorts(
                drain=lambda rec: self._drain(rec),
                review=review,
            ),
        )

    @property
    def _suspect(self) -> bool:
        return self._ctx.state.suspect

    @_suspect.setter
    def _suspect(self, value: bool) -> None:
        self._ctx.state.suspect = value

    @property
    def _agents(self) -> dict[AgentId, ChangeRecord]:
        return self._ctx.state.agents

    @_agents.setter
    def _agents(self, value: dict[AgentId, ChangeRecord]) -> None:
        self._ctx.state.agents = value

    @property
    def _main_graph(self) -> WorkspaceGraph:
        return self._ctx.main_graph

    @_main_graph.setter
    def _main_graph(self, value: WorkspaceGraph) -> None:
        self._ctx.main_graph = value

    @property
    def snapshot(self) -> GraphSnapshot:
        return self._ctx.state.snapshot

    @snapshot.setter
    def snapshot(self, value: GraphSnapshot) -> None:
        self._ctx.state.snapshot = value

    @property
    def main_suspect(self) -> bool:
        """True once a change has entered `broken`, so main's state is unknown, until the process
        restarts and the next start's `reset` rebuilds main. Rejecting that change keeps it true."""
        return self._ctx.state.suspect

    async def open(self, task: AgentTask, model_id: str) -> Tree:
        """Create the agent's worktree from main's HEAD and record its base files and graph."""
        return await proposal.open(self._ctx, task, model_id)

    async def file_edited(self, agent_id: AgentId, path: str) -> bool:
        """Send `change.updated` for an edit on disk; `False` after sending an `error` instead."""
        return await proposal.file_edited(self._ctx, agent_id, path)

    async def tests_executed(self, agent_id: AgentId, run: SuiteRun) -> None:
        rec = self._ctx.state.agents[agent_id]
        async with self._ctx.workspace.locks(agent_id=agent_id):
            await publish_suite(self._ctx, rec, run, phase="agent", revision=None)

    async def proposed(
        self,
        agent_id: AgentId,
        summary: str | None,
        *,
        declaration: FinishDeclaration | None = None,
    ) -> bool:
        """Commit, republish, test and send `change.proposed`; `False` after a fatal `error`."""
        self._ctx.state.agents[agent_id].declared = declaration
        return await proposal.proposed(self._ctx, agent_id, summary)

    def agent_base(self, agent_id: AgentId) -> str | None:
        rec = self._ctx.state.agents.get(agent_id)
        return rec.base_commit if rec is not None else None

    def run_ended(self, agent_id: AgentId, end: RunEnd | None, *, steps: int | None = None) -> None:
        """The run is over (`end` is `None` when it raised or was cancelled): `ready` if this run
        proposed, else `unproposed`. `steps`, the run's own count, wins over `end.steps`. A run
        that a Stop cancelled finds its change `deciding`: that outcome becomes the Stop's
        `fallback`, where a Stop that ends without settling leaves the change."""
        return proposal.run_ended(self._ctx, agent_id, end, steps=steps)

    async def save_for_review(
        self, agent_id: AgentId, path: str, content: str, base_hash: str
    ) -> str:
        """The person's save on the new side in review: write `path` in the agent's worktree,
        commit it as the user and re-propose (R6), so Approve never merges an untested edit; the
        file's new hash. Only from `ready`, checked again under the agent's lock, which the save
        holds throughout (`WrongState`); `StaleFile` when the file is not the one `base_hash` names.
        R9: with nothing to commit and HEAD still the last proposal's, nothing is sent. A save cut
        short after its write is re-proposed by the next Approve (R1)."""
        return await files.save_for_review(self._ctx, agent_id, path, content, base_hash)

    async def read_file(self, agent_id: AgentId | None, path: str) -> FileContent:
        """GET /files. Main is read under main's lock, which every mutation of main holds from its
        first git call to its last publish, so a read sees a whole commit, never the tree of a merge
        waiting for its tests, nor conflict markers before the abort; a suspect main is read as it
        stands. An agent's worktree is read under its own lock, in any state until its cleanup.
        `ChangeNotFound` for no live worktree, `FileNotFoundError` for a missing file, `InvalidPath`
        for a path the workspace refuses and for a file that is not UTF-8 text."""
        return await files.read_file(self._ctx, agent_id, path)

    async def save_main(self, path: str, content: str, base_hash: str) -> FileSaved:
        """PUT /files on main: commit `content` for `path` as the user, a commit holding that file
        only, and publish one `graph.delta` (`user_edit`). One `_whole` step: a cancel of the
        request waits for the save to end, so none falls between the write, the commit and the
        publish. `StaleFile` when the file is missing or is not the one `base_hash` names (a save
        never creates a file), `InvalidPath`, `WrongState` for a suspect, dirty or mid-merge main.
        The same bytes commit nothing. A failed commit restores the file and raises
        `WorkspaceError`; the commit is the save, so a failed rescan after it still answers saved.
        Runs no tests: the next approval tests the combination (R5)."""
        return await files.save_main(self._ctx, path, content, base_hash)

    async def rescan(self) -> None:
        """POST /graph/rescan: read main again and publish one delta (`rescan`) if anything moved:
        a commit made behind the server's back, or one whose scan raised. One `_whole` step. Main
        that is suspect, mid-merge or dirty is not main's graph at any commit: refused with an
        `error` event and nothing published, until the contract lists `wrong_state` for it (P1).
        A scan or publish that raises sends an `error`, keeps `snapshot` and re-raises."""
        return await files.rescan(self._ctx)

    def _take(
        self, change_id: ChangeId, allowed: frozenset[State], verb: str, *, person: bool = True
    ) -> ChangeRecord:
        """Check and flip the state with no `await`, so a second click fails at once."""
        rec = self._ctx.state.changes.get(change_id)
        if rec is None:
            raise ChangeNotFound(change_id)
        if rec.state not in allowed:
            raise WrongState(
                f"{change_id} is {rec.state}; {verb} needs {' or '.join(sorted(allowed))}"
            )
        if person:
            rec.person_intervened()
        rec.fallback, rec.state = rec.state, "deciding"
        rec.decision += 1
        return rec

    def _decide[T](self, rec: ChangeRecord, work: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        """Run a decision in a tracked task. A decision that ends still `deciding`, such as a task
        cancelled before its first step, which runs none of its code, goes back to `fallback`
        through a done-callback, which runs before anyone awaiting the task resumes. The callback
        keeps its decision's number: a decision that settled, then took a while to report it, ends
        after the next one was taken, and must leave that one `deciding`."""
        task = self._ctx.spawn(work)
        decision = rec.decision
        task.add_done_callback(lambda _: self._ctx.state.restore(rec, decision))
        return task

    async def capture_review(self, change_id: ChangeId) -> ReviewCapture:
        return await cross_review.capture(self._ctx, change_id)

    def track_review(self, capture: ReviewCapture, task: asyncio.Task[Any]) -> None:
        """Attach the runtime-owned task so human admission cancels it without awaiting."""
        cross_review.current(self._ctx, capture).review_task = task

    def review_current(self, capture: ReviewCapture, *, require_finished: bool = True) -> bool:
        try:
            cross_review.current(self._ctx, capture, require_finished=require_finished)
        except WrongState:
            return False
        return True

    async def review_diff(self, capture: ReviewCapture) -> str:
        """Read only the captured, clean worktree, under the usual main → worktree locks."""
        rec = cross_review.current(self._ctx, capture)
        async with self._ctx.workspace.locks(main=True, agent_id=rec.agent_id):
            cross_review.current(self._ctx, capture)
            await cross_review.verify_tree(self._ctx, capture)
            diff = await self._ctx.git(
                rec.tree,
                "diff",
                "--no-ext-diff",
                capture.target.base_commit,
                capture.target.head_commit,
            )
            cross_review.current(self._ctx, capture)
            return diff

    def request_cross_review(
        self, capture: ReviewCapture, outcome: ReviewOutcome, *, review_seq: int
    ) -> asyncio.Task[Revision | None]:
        """Server actor used by runtime admission; models have no decision tool."""
        try:
            rec = cross_review.prepare(self._ctx, capture, outcome, review_seq)
        except cross_review.RoundExhausted:
            return self._ctx.spawn(cross_review.exhausted(self._ctx, capture))
        self._take(rec.change_id, APPROVABLE, "cross-review request", person=False)
        task = self._decide(rec, self._request_changes(rec, outcome.decision.comment))
        task.add_done_callback(lambda done: cross_review.release(rec, done))
        return task

    def approve(
        self, change_id: ChangeId, comment: str | None, *, revision: int, head_commit: str
    ) -> asyncio.Task[None]:
        current = self._ctx.state.changes.get(change_id)
        if current is None:
            raise ChangeNotFound(change_id)
        validate_decision_target(current, revision, head_commit, APPROVABLE)
        evidence = WriteEvidence(
            change_id,
            current.agent_id,
            revision,
            current.base_commit,
            head_commit,
            "person",
            "approved",
            comment,
        )
        validate_resolution_intent(evidence)
        rec = self._take(change_id, APPROVABLE, "approve")
        rec.evidence = evidence
        rec.taken = rec.revision
        return self._decide(rec, approval.approve(self._ctx, rec, comment))

    def reject(
        self, change_id: ChangeId, comment: str | None, *, revision: int, head_commit: str
    ) -> asyncio.Task[None]:
        current = self._ctx.state.changes.get(change_id)
        if current is None:
            raise ChangeNotFound(change_id)
        validate_decision_target(current, revision, head_commit, REJECTABLE)
        evidence = WriteEvidence(
            change_id,
            current.agent_id,
            revision,
            current.base_commit,
            head_commit,
            "person",
            "rejected",
            comment,
        )
        validate_resolution_intent(evidence)
        rec = self._take(change_id, REJECTABLE, "reject")
        rec.evidence = evidence
        return self._decide(rec, self._reject(rec, comment))

    def request_changes(
        self, change_id: ChangeId, comment: str, *, revision: int, head_commit: str
    ) -> asyncio.Task[Revision | None]:
        """Send the comment back to the agent; its worktree, branch, base and files stay. From
        `conflict`, main is merged into the agent's branch first. The task returns what the agent
        resumes with, the change `working`; or `None` after an `error` when the base could not
        move, the change still `conflict` (or `broken`, when the worktree's state is unknown), or
        when main turned suspect while the request queued, the change still as it was taken."""
        current = self._ctx.state.changes.get(change_id)
        if current is None:
            raise ChangeNotFound(change_id)
        validate_decision_target(current, revision, head_commit, REVISABLE)
        evidence = WriteEvidence(
            change_id,
            current.agent_id,
            revision,
            current.base_commit,
            head_commit,
            "person",
            "changes_requested",
            comment,
        )
        validate_resolution_intent(evidence)
        rec = self._take(change_id, REVISABLE, "request changes")
        rec.evidence = evidence
        return self._decide(rec, self._request_changes(rec, comment))

    def stop(self, agent_id: AgentId, run: asyncio.Task[None] | None) -> asyncio.Task[None]:
        """Stop the agent and discard its change: from what Reject takes, or from `working`, whose
        live `run` is cancelled in this same call. The task waits for the run's end, so the
        resolution names the revision the store took (R7), then closes out as Reject does, with
        `reason="stopped"`, `agent.status` `stopped` before the resolution, and no `done`."""
        rec = self._ctx.state.agents.get(agent_id)
        if rec is None:
            raise ChangeNotFound(agent_id)
        self._take(rec.change_id, STOPPABLE, "stop")
        if run is None:
            proposal.unreported(self._ctx, rec)
        else:
            run.cancel()
            # Registered here, not in the Stop's task, which a cancel can end before its body or in
            # its wait for the run. Before the wait's own callback, so it settles first.
            run.add_done_callback(lambda _: proposal.unreported(self._ctx, rec))
        return self._decide(rec, self._stop(rec, run))

    async def check_main(self) -> bool | None:
        """Under main's lock: True when main has no `MERGE_HEAD` and `status --porcelain` is empty,
        False otherwise, None when git fails or times out."""
        return await files.check_main(self._ctx)

    async def aclose(self) -> None:
        """Drain before the store closes, which the caller does only once this returns: wait for
        the decisions in flight, run the close-out retry once more for each change with unsent
        events, then send one `error` per change naming each event still unsent. If the store
        refuses that `error` too, one log line per change names what was lost. Cleanup is left
        to the next start's `reset`."""
        return await delivery.aclose(self._ctx)

    async def _request_changes(self, rec: ChangeRecord, comment: str) -> Revision | None:
        return await revision.request_changes(self._ctx, rec, comment)

    async def _reject(self, rec: ChangeRecord, comment: str | None) -> None:
        await delivery.finish_pending(self._ctx)
        async with self._ctx.workspace.locks(main=True, agent_id=rec.agent_id):
            assert rec.evidence is not None
            try:
                validate_decision_target(
                    rec, rec.evidence.revision, rec.evidence.head_commit, REJECTABLE, reserved=True
                )
            except WrongState as error:
                await self._ctx.error("server", str(error), rec, fatal=False)
                return
            validate_resolution_intent(rec.evidence)
            rec.resolution = delivery.resolution(
                self._ctx, rec, "rejected", comment, decided_by="person"
            )
            await delivery.finish(self._ctx, rec)  # main did not change: no rescan and no delta

    async def _stop(self, rec: ChangeRecord, run: asyncio.Task[None] | None) -> None:
        if run is not None:
            # Its end calls `run_ended`, which records its steps and sets `fallback`; a run cut in
            # its proposal's publish records the proposal first (R7). A run that never started
            # reports nothing: `stop` settled it with a done-callback, which ran before this wait
            # returned, so `fallback` is ready or unproposed from here on.
            await asyncio.wait([run])
        await delivery.finish_pending(self._ctx)
        async with self._ctx.workspace.locks(main=True, agent_id=rec.agent_id):
            # Under the agent's lock: a save in review proposes first, and this closes its revision.
            rec.evidence = WriteEvidence(
                rec.change_id,
                rec.agent_id,
                rec.revision,
                rec.base_commit,
                rec.head_commit or rec.base_commit,
                "person",
                "rejected",
                None,
            )
            validate_resolution_intent(rec.evidence)
            rec.resolution = delivery.resolution(
                self._ctx, rec, "rejected", None, decided_by="person", reason="stopped"
            )
            rec.stopped = True
            await delivery.finish(self._ctx, rec)

    @staticmethod
    async def _drain(rec: ChangeRecord) -> None:
        """Wait for `rec`'s close-out publish in flight, if any; its outcome is recorded when this
        returns. A cancel ends the wait and leaves the publish on the record."""
        if rec.sending is not None:
            await asyncio.wait([rec.sending])
