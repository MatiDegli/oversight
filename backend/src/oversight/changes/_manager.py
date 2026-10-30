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
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from oversight.agents import AgentTask, Publish, ReviewOutcome, RunEnd
from oversight.changes import _approval as approval
from oversight.changes import _cross_review as cross_review
from oversight.changes import _delivery as delivery
from oversight.changes import _files as files
from oversight.changes import _proposal as proposal
from oversight.changes import _revision as revision
from oversight.changes import _rules as rules
from oversight.changes import _transaction as transaction
from oversight.changes._answers import require_yes, validate_answers
from oversight.changes._assessment import RulesScheduler
from oversight.changes._decision_validation import validate_decision_target
from oversight.changes._evidence import WriteEvidence
from oversight.changes._mode import ModeController
from oversight.changes._resolution_validation import validate_resolution_intent
from oversight.changes._review_cards import DifferenceDecisions, ReviewCardLimits, ReviewCards
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
from oversight.changes._summary_capture import record_usage
from oversight.changes._support import ChangeContext, ChangePorts, RulesFailure
from oversight.events import Clock, EventPayload, StoredEvent, system_clock
from oversight.graph import (
    WorkspaceGraph,
)
from oversight.workspace import (
    SuiteRun,
    Tree,
    Workspace,
)
from oversight_contracts.commands import FileContent, FileSaved, SetMergeMode
from oversight_contracts.common import AgentId, ChangeId
from oversight_contracts.grants import GrantCreated, GrantRequest
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.policy import ChangeAssessment, MergeModeState
from oversight_contracts.review import ChangeTarget, FinishDeclaration, ReviewCard


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
        behavior_diff: bool = False,
        measured_cone: bool = False,
        difference_decisions: DifferenceDecisions | None = None,
        review_card_limits: ReviewCardLimits | None = None,
        run_id: str = "",
        monotonic: Callable[[], float] = time.monotonic,
        sleep_until: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        async def committed(payload: EventPayload) -> object:
            receipt = await publish(payload)
            if isinstance(receipt, StoredEvent):
                self.rules_received(receipt)
            return receipt

        async def confirmed(payload: EventPayload) -> object:
            cards = self._ctx.cards
            return await cards.publish(payload) if cards is not None else await committed(payload)

        self._ctx = ChangeContext(
            workspace,
            confirmed,
            main_graph,
            clock,
            git_timeout_s,
            ChangeState(main_snapshot),
            ChangePorts(
                drain=lambda rec: self._drain(rec),
                review=review,
                rules_admission=self._admit_rules,
                rules_transaction=lambda rec, evidence: transaction.run(self._ctx, rec, evidence),
            ),
            ModeController(),
            behavior_diff,
            measured_cone,
        )

        self._ctx.assessment = RulesScheduler(
            self._ctx,
            monotonic,
            sleep_until or (lambda deadline: asyncio.sleep(max(0, deadline - monotonic()))),
        )
        self._ctx.grants.run_id = run_id
        if difference_decisions is not None:
            if not run_id:
                raise ValueError("review cards require the current run id")
            self._ctx.cards = ReviewCards(
                self._ctx,
                run_id,
                difference_decisions,
                review_card_limits or ReviewCardLimits(),
                committed,
            )

    def rules_received(self, receipt: StoredEvent, *, generation: int | None = None) -> None:
        """Port for committed external facts, reviews and combined suite receipts."""
        self._ctx.assessment.received(receipt, generation=generation)

    def usage_recorded(self, receipt: StoredEvent) -> None:
        """Retain committed usage independently of rules assessment."""
        record_usage(self._ctx.state, receipt)

    def merge_mode(self) -> MergeModeState:
        """The confirmed mode/revision for GET and future proposal capture."""
        return self._ctx.mode.snapshot()

    async def set_merge_mode(self, command: SetMergeMode) -> MergeModeState:
        return await self._ctx.mode.set(self._ctx, command)

    def authority_suspended(self) -> bool:
        return self._ctx.mode.authority_suspended()

    @property
    def _suspect(self) -> bool:
        return self.main_suspect

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
        return self._ctx.state.suspect or self._ctx.workspace.integrity.main_suspect

    async def open(
        self, task: AgentTask, model_id: str, *, before_create: Callable[[], None] | None = None
    ) -> Tree:
        """Create the agent's worktree from main's HEAD and record its base files and graph."""
        return await proposal.open(self._ctx, task, model_id, before_create=before_create)

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

    def change_graph(self, change_id: ChangeId) -> GraphSnapshot:
        """An independent snapshot of the change's current version, also used by its proposal.

        No disk read or await: a concurrent refresh replaces the version atomically.
        """
        rec = self._ctx.state.changes.get(change_id)
        if rec is None:
            raise ChangeNotFound(change_id)
        return rec.head.graph.model_copy(deep=True)

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
        self._ctx.assessment.invalidate(rec)
        rec.invalidate_cards()
        if person:
            rec.person_intervened()
        rec.fallback, rec.state = rec.state, "deciding"
        rec.decision += 1
        return rec

    def _decide[T](self, rec: ChangeRecord, work: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        """Restore before completion, even if awaiting an already done task does not yield.

        The callback also covers cancellation before the coroutine's first step. Both paths
        keep the decision number, so an older decision cannot restore a newer one.
        """
        decision = rec.decision

        async def deciding() -> T:
            try:
                return await work
            finally:
                self._ctx.state.restore(rec, decision)

        def done(_: asyncio.Task[T]) -> None:
            work.close()  # a pre-start cancellation never awaits the inner coroutine
            self._ctx.state.restore(rec, decision)

        task = self._ctx.spawn(deciding())
        task.add_done_callback(done)
        return task

    async def capture_review(self, change_id: ChangeId) -> ReviewCapture:
        return await cross_review.capture(self._ctx, change_id)

    def track_review(
        self,
        capture: ReviewCapture,
        task: asyncio.Task[Any],
        *,
        require_finished: bool = True,
    ) -> None:
        """Attach the runtime-owned task so human admission cancels it without awaiting."""
        rec = cross_review.current(self._ctx, capture, require_finished=require_finished)
        rec.review_task = task

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

    def _decision_answers(
        self, rec: ChangeRecord, answers: list[ReviewCard] | None, *, approve: bool = False
    ) -> tuple[ReviewCard, ...] | None:
        job = self._ctx.cards.for_change(rec) if self._ctx.cards else None
        cards = (job.facts.review_cards or []) if job and job.facts else []
        accepted = validate_answers(cards, answers)
        if approve:
            require_yes(cards, accepted, pending=job is not None and job.preparing)
        return tuple(accepted) if accepted is not None else None

    def approve(
        self,
        change_id: ChangeId,
        comment: str | None,
        *,
        revision: int,
        head_commit: str,
        answers: list[ReviewCard] | None = None,
        grant: GrantRequest | None = None,
    ) -> asyncio.Task[None]:
        current = self._ctx.state.changes.get(change_id)
        if current is None:
            raise ChangeNotFound(change_id)
        validate_decision_target(current, revision, head_commit, APPROVABLE)
        accepted = self._decision_answers(current, answers, approve=True)
        created = self._ctx.grants.validate(self._ctx, current, grant) if grant else None
        evidence = WriteEvidence(
            change_id,
            current.agent_id,
            revision,
            current.base_commit,
            head_commit,
            "person",
            "approved",
            comment,
            answers=accepted,
        )
        validate_resolution_intent(evidence)
        rec = self._take(change_id, APPROVABLE, "approve")
        rec.evidence = evidence
        rec.taken = rec.revision
        if created is not None:
            self._ctx.grants.counter += 1
        return self._decide(rec, self._approve_person(rec, comment, created))

    async def _approve_person(
        self, rec: ChangeRecord, comment: str | None, created: GrantCreated | None
    ) -> None:
        if created is not None:
            try:
                await self._ctx.whole(self._ctx.grants.create(self._ctx, created))
            except Exception as error:
                await self._ctx.error(
                    "server",
                    f"Grant creation failed; approval merged nothing: {error}",
                    rec,
                    fatal=False,
                )
                return
        await approval.approve(self._ctx, rec, comment)

    async def revoke_grant(self, grant_id: str) -> None:
        await self._ctx.whole(self._ctx.grants.revoke(self._ctx, grant_id))

    def _admit_rules(
        self, rec: ChangeRecord, assessment: ChangeAssessment
    ) -> tuple[str, str] | None:
        wait = self._ctx.assessment.waits.get(rec.change_id)
        if wait is None or not self._ctx.assessment.valid(wait):
            return None
        job = self._ctx.cards.for_change(rec) if self._ctx.cards else None
        if job is not None:
            if job.preparing:
                return (
                    "policy_invalid",
                    "Review questions are still being prepared; review this change yourself.",
                )
            if job.facts and job.facts.review_cards:
                return (
                    "policy_invalid",
                    "Review questions need the person's answers; review this change yourself.",
                )
            if job.status in {"failed", "truncated"}:
                return (
                    "policy_invalid",
                    "Review questions could not be completed; review this change yourself.",
                )
        try:
            reservation = rules.capture(self._ctx, wait, assessment)
        except RulesFailure as error:
            return error.code, str(error)
        except (ValueError, WrongState) as error:
            return "policy_invalid", str(error)
        try:
            self._take(rec.change_id, APPROVABLE, "rules approval", person=False)
        except WrongState:
            return None
        rec.taken = rec.revision
        rec.rules_consumed.add(rec.revision)
        rec.evidence = reservation.evidence
        self._decide(rec, approval.approve_rules(self._ctx, reservation))
        return None

    def reject(
        self,
        change_id: ChangeId,
        comment: str | None,
        *,
        revision: int,
        head_commit: str,
        answers: list[ReviewCard] | None = None,
    ) -> asyncio.Task[None]:
        current = self._ctx.state.changes.get(change_id)
        if current is None:
            raise ChangeNotFound(change_id)
        validate_decision_target(current, revision, head_commit, REJECTABLE)
        accepted = self._decision_answers(current, answers)
        evidence = WriteEvidence(
            change_id,
            current.agent_id,
            revision,
            current.base_commit,
            head_commit,
            "person",
            "rejected",
            comment,
            answers=accepted,
        )
        validate_resolution_intent(evidence)
        rec = self._take(change_id, REJECTABLE, "reject")
        rec.evidence = evidence
        return self._decide(rec, self._reject(rec, comment))

    def request_changes(
        self,
        change_id: ChangeId,
        comment: str,
        *,
        revision: int,
        head_commit: str,
        answers: list[ReviewCard] | None = None,
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
        accepted = self._decision_answers(current, answers)
        evidence = WriteEvidence(
            change_id,
            current.agent_id,
            revision,
            current.base_commit,
            head_commit,
            "person",
            "changes_requested",
            comment,
            answers=accepted,
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
        if self._ctx.grants.entries:
            async with self._ctx.mode.held():
                self._ctx.grants.run_id = ""
        else:
            self._ctx.grants.run_id = ""
        await self.close_review_cards()
        return await delivery.aclose(self._ctx)

    async def close_review_cards(self) -> None:
        if self._ctx.cards is not None:
            await self._ctx.cards.close()

    async def update_facts(self, target: ChangeTarget, updates: dict[str, object]) -> StoredEvent:
        """Compose producer updates onto the latest captured facts without losing other fields."""
        if self._ctx.cards is None:
            raise WrongState("Review card facts composition is unavailable")
        return await self._ctx.cards.update(target, updates)

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
