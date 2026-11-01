"""Rules waits: confirmed evidence, a fixed deadline, and a closed admission boundary."""

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from pydantic import TypeAdapter, ValidationError

from oversight.agents import FAKE_MODEL, FAKE_MODEL_B, MUSE_SPARK, MUSE_SPARK_OPEN
from oversight.changes import _follow, _spend
from oversight.changes._facts import AddedTestsInventory, FactsEvidence, added_tests_inventory
from oversight.changes._grants import Measurement, measure
from oversight.changes._policy import PolicyInputs, assess
from oversight.changes._protected_paths import (
    ChangedFileInventory,
    ProtectedPathSnapshot,
    capture_proposal_paths,
)
from oversight.changes._shortcuts_common import is_test_file
from oversight.changes._state import ChangeRecord
from oversight.changes._support import RulesFailure
from oversight.events import NeedsYouWait, StoredEvent, format_needs_you
from oversight_contracts.common import FileStatus, RuleId
from oversight_contracts.events import ChangeProposed, ErrorReport, RunStartedEvent, ServerEvent
from oversight_contracts.grants import covered_rule
from oversight_contracts.policy import ChangeAssessment, EvidenceRef, MergeModeState

if TYPE_CHECKING:
    from oversight.changes._support import ChangeContext

PROFILE = "safe-merge-paths-v1"
EVIDENCE_TIMEOUT_S = 120
MAX_EVIDENCE_EVENTS = 1024
EXPIRED = "Required rules evidence was not confirmed within {seconds:g} seconds."
_log = logging.getLogger(__name__)
_EVENT = TypeAdapter(ServerEvent)
_EXISTING_TEST_STATUS: dict[FileStatus, Literal["modified", "deleted"] | None] = {
    "added": None,
    "modified": "modified",
    "deleted": "deleted",
}


@dataclass(frozen=True)
class RulesEvidence:
    inputs: PolicyInputs
    unavailable: str | None = None
    """A producer's permanent configuration failure, never a missing observation."""


def closed_admission(rec: ChangeRecord, assessment: ChangeAssessment) -> tuple[str, str]:
    return "policy_invalid", "Rules admission is unavailable; reservation is not connected (P2b-2)."


@dataclass
class AssessmentWait:
    rec: ChangeRecord
    target: NeedsYouWait
    mode: MergeModeState
    proposal: ChangeProposed
    proposal_seq: int | None
    main_commit: str
    active: bool = True
    deadline: float | None = None
    timer: asyncio.Task[None] | None = None
    task: asyncio.Task[None] | None = None
    dirty: bool = False
    facts: FactsEvidence | None = None
    facts_seq: int | None = None
    inventory: AddedTestsInventory | None = None
    parse_errors: int | None = None
    unresolved_removed: int | None = None
    possible_callers: int | None = None
    grant_measurement: Measurement | None = None
    latest: ChangeAssessment | None = None
    terminal_seq: int | None = None
    spend_pin: StoredEvent | None = None
    paths: tuple[ProtectedPathSnapshot, ChangedFileInventory] | None = None
    parked: bool = False


class RulesScheduler:
    def __init__(
        self,
        ctx: "ChangeContext",
        monotonic: Callable[[], float],
        sleep_until: Callable[[float], Awaitable[None]],
    ) -> None:
        self.ctx, self.monotonic, self.sleep_until = ctx, monotonic, sleep_until
        self.waits: dict[str, AssessmentWait] = {}
        self.events: dict[int, ServerEvent] = {}
        self.spend_receipt: StoredEvent | None = None
        self.spend_receipt_total: float | None = None
        self.run_started_seq: int | None = None
        self.table_receipt: StoredEvent | None = None
        self._evicted_through = 0
        self.generation = 0
        self.committed_main: str | None = None

    def spend_received(self, stored: StoredEvent) -> None:
        """Pin the run's table receipt and eligible spend provenance outside the event cache.

        Usage envelopes have no run id: provenance is the owning LiveRun's explicit source
        sites, never a role/model heuristic. The seed additionally identifies that run.
        The run receipt identifies the table independently of spend eligibility. Neither
        pinned receipt supplies a current durable spend observation.
        """
        if stored.type not in {"run.started", "usage.tick"}:
            return
        try:
            event = _EVENT.validate_json(stored.envelope)
        except ValidationError:
            return
        if (event.type, event.seq, event.ts) != (stored.type, stored.seq, stored.ts):
            return
        if event.type == "run.started":
            if event.payload.run_id != self.ctx.grants.run_id:
                return
            if self.run_started_seq is None:
                self.run_started_seq = event.seq
                self.table_receipt = stored
            ledger = self.ctx.ports.spend_ledger
            if ledger is not None and ledger.state is None:
                # Startup's display fallback is not durable evidence of zero spend.
                return
            total, budget = event.payload.budget.spent_usd, event.payload.budget.budget_usd
        elif event.type == "usage.tick":
            total, budget = event.payload.spent_usd, event.payload.budget_usd
        else:
            return
        if self.spend_receipt is not None and stored.seq <= self.spend_receipt.seq:
            return
        if not math.isfinite(total) or total < 0 or not math.isfinite(budget) or budget <= 0:
            return
        self.spend_receipt, self.spend_receipt_total = stored, total
        self.spend_changed()

    def run_started(self) -> RunStartedEvent | None:
        stored = self.table_receipt
        if stored is None:
            return None
        try:
            event = _EVENT.validate_json(stored.envelope)
        except ValidationError:
            return None
        if (
            event.type != "run.started"
            or (event.type, event.seq, event.ts) != (stored.type, stored.seq, stored.ts)
            or event.seq != self.run_started_seq
            or event.payload.run_id != self.ctx.grants.run_id
        ):
            return None
        return event

    def admits(self, rule: RuleId, ref: EvidenceRef, pin: StoredEvent | None) -> bool:
        """Table evidence has its own pin; spend admission remains owned by F3b."""
        if rule == "protected_paths" and ref.event_seq == self.run_started_seq:
            return self.run_started() is not None and ref.git_object is None
        return _spend.admits(self.ctx, rule, ref, pin)

    def spend_changed(self) -> None:
        for wait in self.waits.values():
            self.wake(wait.rec)

    def proposed(
        self, rec: ChangeRecord, proposal: ChangeProposed, mode: MergeModeState, *, armed: bool
    ) -> None:
        self.invalidate(rec)
        rec.proposal_mode = mode
        if not armed or mode.mode != "merge_when_safe" or mode != self.ctx.mode.snapshot():
            return
        self.generation += 1
        seq = next(
            (
                seq
                for seq, event in self.events.items()
                if event.type == "change.proposed" and event.payload == proposal
            ),
            None,
        )
        self.waits[rec.change_id] = AssessmentWait(
            rec,
            NeedsYouWait(rec.change_id, rec.revision, proposal.head_commit, self.generation),
            mode,
            proposal.model_copy(deep=True),
            seq,
            self.ctx.state.snapshot.commit,
            parked=self.committed_main not in (None, self.ctx.state.snapshot.commit),
            paths=capture_proposal_paths(
                proposal, proposal_seq=seq, run_started_seq=self.run_started_seq
            ),
        )
        self.wake(rec)

    def path_sources(
        self, wait: AssessmentWait
    ) -> tuple[ProtectedPathSnapshot, ChangedFileInventory] | None:
        """Use only the original capture while both committed receipts still match exactly."""
        rec, proposal = wait.rec, wait.proposal
        event = self.events.get(wait.proposal_seq) if wait.proposal_seq is not None else None
        run = self.run_started()
        if (
            wait.paths is None
            or event is None
            or event.type != "change.proposed"
            or event.payload != proposal
            or run is None
            or run.type != "run.started"
            or run.payload.run_id != self.ctx.grants.run_id
            or (rec.change_id, rec.agent_id, rec.revision, rec.base_commit, rec.head_commit)
            != (
                proposal.change_id,
                proposal.agent_id,
                proposal.revision,
                proposal.base_commit,
                proposal.head_commit,
            )
            or wait.paths[0].evidence != (EvidenceRef(event_seq=run.seq),)
            or wait.paths[1].evidence != (EvidenceRef(event_seq=event.seq),)
            or wait.paths[1].files != tuple(proposal.files)
        ):
            return None
        return wait.paths

    def received(self, stored: StoredEvent, *, generation: int | None = None) -> None:
        """Notification port: callers pass only receipts returned after the store commits."""
        if stored.type not in {
            "change.proposed",
            "change.facts_recorded",
            "change.reviewed",
            "tests.executed",
            "change.resolved",
            "error",
        }:
            return
        cached = self.events.get(stored.seq)
        duplicate = cached is not None or stored.seq <= self._evicted_through
        try:
            event = cached if cached is not None else _EVENT.validate_json(stored.envelope)
        except ValidationError:
            _log.warning(
                "Dropped rules receipt seq=%s type=%s: validation", stored.seq, stored.type
            )
            return
        if (event.seq, event.ts, event.type) != (stored.seq, stored.ts, stored.type):
            _log.warning("Dropped rules receipt seq=%s type=%s: mismatch", stored.seq, stored.type)
            return
        if not duplicate:
            self.events[event.seq] = event
            if len(self.events) > MAX_EVIDENCE_EVENTS:
                oldest = next(iter(self.events))
                del self.events[oldest]
                self._evicted_through = max(self._evicted_through, oldest)
        # Evicted evidence becomes unknown; old receipts cannot restart an evaluation.
        # Terminal acknowledgments still recognize their exact target and generation.
        payload = event.payload
        change_id = getattr(payload, "change_id", None)
        rec = self.ctx.state.changes.get(change_id) if isinstance(change_id, str) else None
        if rec is None:
            return
        wait = self.waits.get(rec.change_id)
        if wait is None:
            return
        if event.type == "error" and wait.target.accepts(
            event.payload, generation=wait.target.generation if generation is None else generation
        ):
            wait.terminal_seq = event.seq
            self.invalidate(rec)
            wait.spend_pin = None
        elif duplicate:
            return
        elif event.type == "change.resolved" and (
            event.payload.revision,
            event.payload.head_commit,
        ) == (wait.target.revision, wait.target.head):
            self.invalidate(rec)
            wait.spend_pin = None
        elif event.type in {
            "change.proposed",
            "change.facts_recorded",
            "change.reviewed",
            "tests.executed",
        }:
            self.wake(rec)

    def facts_ready(self, rec: ChangeRecord, evidence: FactsEvidence) -> None:
        wait = self.waits.get(rec.change_id)
        if wait is None or (
            evidence.target.revision,
            evidence.target.base_commit,
            evidence.target.head_commit,
        ) != (wait.target.revision, wait.proposal.base_commit, wait.target.head):
            return
        seq = next(
            (
                seq
                for seq, event in reversed(self.events.items())
                if event.type == "change.facts_recorded"
                and event.payload.change_id == rec.change_id
                and event.payload.revision == rec.revision
            ),
            None,
        )
        if seq is not None:
            wait.facts, wait.facts_seq = evidence, seq
            wait.inventory = added_tests_inventory(rec, rec.revision)
            wait.parse_errors = len(rec.head.graph.parse_errors)
            wait.grant_measurement = measure(
                rec.base.graph,
                rec.head.graph,
                rec.graph,
                [f.path for f in wait.proposal.files],
                wait.proposal.impact,
            )
            wait.possible_callers = wait.grant_measurement.caller_count
            wait.unresolved_removed = 0 if not wait.proposal.graph.nodes_removed else None
            self.wake(rec)

    def valid(
        self,
        wait: AssessmentWait,
        *,
        check_main: bool = True,
        parked_ok: bool = False,
        working_ok: bool = False,
    ) -> bool:
        rec = wait.rec
        return (
            wait.active
            and self.waits.get(rec.change_id) is wait
            and not self.ctx.state.closing
            and rec.person_intent is None
            and (parked_ok or not wait.parked)
            and (rec.state in {"ready", "working"} if working_ok else rec.state == "ready")
            and (not check_main or wait.main_commit == self.ctx.state.snapshot.commit)
            and rec.resolution is None
            and (
                wait.parked
                or (rec.revision, rec.head_commit, wait.main_commit, wait.mode.revision)
                not in rec.rules_attempts
            )
            and (rec.revision, rec.head_commit, rec.base_commit)
            == (wait.target.revision, wait.target.head, wait.proposal.base_commit)
            and rec.proposal_mode == wait.mode == self.ctx.mode.snapshot()
            and not self.ctx.mode.authority_suspended()
        )

    def wake(self, rec: ChangeRecord) -> None:
        wait = self.waits.get(rec.change_id)
        if wait is None or not self.valid(wait, check_main=not wait.parked, parked_ok=True):
            return
        if wait.deadline is None:
            wait.deadline = self.monotonic() + EVIDENCE_TIMEOUT_S
            self.start_timer(wait)
        if wait.parked:
            return
        wait.dirty = True
        if wait.task is None or wait.task.done():
            wait.task = self.ctx.spawn(self.evaluate(wait))
            wait.task.add_done_callback(self._observe_task)

    def start_timer(self, wait: AssessmentWait) -> None:
        wait.timer = self.ctx.spawn(self.expire(wait))
        wait.timer.add_done_callback(self._observe_task)

    def main_changed(self, post: str) -> None:
        _follow.changed(self, post)

    def main_delivered(self, post: str) -> None:
        _follow.delivered(self, post)

    def decision_finished(self, rec: ChangeRecord, decision: int) -> None:
        _follow.decision_finished(self, rec, decision)

    @staticmethod
    def _observe_task(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            _log.error(
                "Rules scheduler task failed", exc_info=(type(error), error, error.__traceback__)
            )

    def invalidate(self, rec: ChangeRecord) -> None:
        rec.follow_pending = None
        wait = self.waits.get(rec.change_id)
        if wait is not None:
            wait.active = False
            for task in (wait.timer, wait.task):
                if task is not None and task is not asyncio.current_task():
                    task.cancel()

    def mode_changed(self) -> None:
        for wait in self.waits.values():
            self.invalidate(wait.rec)

    async def expire(self, wait: AssessmentWait) -> None:
        assert wait.deadline is not None

        def live() -> bool:
            return self.valid(wait, check_main=not wait.parked, parked_ok=True)

        while live() and self.monotonic() < wait.deadline:
            await self.sleep_until(wait.deadline)
        if live():
            await self.report_terminal(
                wait, "policy_invalid", EXPIRED.format(seconds=EVIDENCE_TIMEOUT_S), live
            )

    async def terminal(self, wait: AssessmentWait, reason: str) -> None:
        await self.report_terminal(wait, "policy_invalid", reason, lambda: self.valid(wait))

    async def report_terminal(
        self, wait: AssessmentWait, code: str, reason: str, current: Callable[[], bool]
    ) -> None:
        from oversight.changes._rules import availability

        availability(self.ctx)
        # No await between this final validation and entering the publisher's target claim.
        if not current():
            return
        receipt = await self.ctx.publish(
            ErrorReport(
                source="server",
                agent_id=wait.rec.agent_id,
                change_id=wait.rec.change_id,
                fatal=False,
                message=format_needs_you(wait.target.revision, wait.target.head, code, reason),
            )
        )
        if isinstance(receipt, StoredEvent):
            self.received(receipt, generation=wait.target.generation)

    def inputs(self, wait: AssessmentWait) -> PolicyInputs:
        rec, proposal, facts = wait.rec, wait.proposal, wait.facts
        paths = self.path_sources(wait)
        refs = (EvidenceRef(event_seq=wait.proposal_seq),) if wait.proposal_seq else ()
        evidence: dict[RuleId, tuple[EvidenceRef, ...]] = dict.fromkeys(
            ("small_change", "small_cone", "existing_tests_untouched"), refs
        )
        inputs = PolicyInputs(
            change_id=rec.change_id,
            agent_id=rec.agent_id,
            revision=wait.target.revision,
            base_commit=proposal.base_commit,
            head_commit=wait.target.head,
            main_commit=wait.main_commit,
            mode=wait.mode.mode,
            mode_revision=wait.mode.revision,
            cone=proposal.impact,
            files=tuple(f.path for f in proposal.files),
            lines_added=proposal.lines_added,
            lines_removed=proposal.lines_removed,
            modified_existing_test_paths=tuple(
                f.path
                for f in proposal.files
                if is_test_file(f.path)
                and _EXISTING_TEST_STATUS.get(f.status, "modified") == "modified"
            ),
            deleted_existing_test_paths=tuple(
                f.path
                for f in proposal.files
                if is_test_file(f.path) and _EXISTING_TEST_STATUS.get(f.status) == "deleted"
            ),
            evidence=evidence,
            protected_paths=paths[0] if paths else None,
            changed_files=paths[1] if paths else None,
        )
        if facts is not None and wait.facts_seq is not None:
            refs = (EvidenceRef(event_seq=wait.facts_seq),)
            inputs = replace(
                inputs,
                candidate_tree=facts.target.candidate_tree,
                integrity=facts.integrity,
                integrity_status=facts.integrity_status,
                added_tests=facts.added_tests,
                added_tests_inventory=wait.inventory,
                parse_errors=wait.parse_errors,
                possible_callers=wait.possible_callers,
                unresolved_removed=wait.unresolved_removed,
                evidence={
                    **inputs.evidence,
                    "test_integrity": refs,
                    "added_tests_fail_before": refs,
                },
            )
        return self.later_inputs(wait, inputs)

    def later_inputs(self, wait: AssessmentWait, inputs: PolicyInputs) -> PolicyInputs:
        rec = wait.rec
        for seq, event in self.events.items():
            if event.type == "change.reviewed" and (
                event.payload.change_id,
                event.payload.revision,
                event.payload.head_commit,
            ) == (rec.change_id, rec.revision, rec.head_commit):
                author = next(
                    (
                        p
                        for p in (MUSE_SPARK, MUSE_SPARK_OPEN, FAKE_MODEL, FAKE_MODEL_B)
                        if p.id == rec.model_id
                    ),
                    None,
                )
                inputs = replace(
                    inputs,
                    review=event.payload,
                    author=author,
                    evidence={**inputs.evidence, "cross_review": (EvidenceRef(event_seq=seq),)},
                )
            elif (
                event.type == "tests.executed"
                and event.payload.phase == "combined"
                and (
                    event.payload.change_id,
                    event.payload.revision,
                    event.payload.tested_commit,
                    event.payload.tested_tree,
                )
                == (
                    rec.change_id,
                    rec.revision,
                    self.ctx.state.snapshot.commit,
                    inputs.candidate_tree,
                )
            ):
                inputs = replace(
                    inputs,
                    suite=event.payload.suite,
                    evidence={**inputs.evidence, "combined_tests": (EvidenceRef(event_seq=seq),)},
                )
        return inputs

    async def evaluate(self, wait: AssessmentWait) -> None:
        while wait.dirty and self.valid(wait):
            wait.dirty = False
            try:
                assert wait.deadline is not None
                deadline = asyncio.get_running_loop().time() + max(
                    0, wait.deadline - self.monotonic()
                )
                await _spend.ready(self.ctx, deadline=deadline)
            except RulesFailure as error:
                await self.report_terminal(wait, error.code, str(error), lambda: self.valid(wait))
                return
            if not self.valid(wait):
                return
            inputs = self.inputs(wait)
            inputs, pin, unavailable = _spend.sample(self.ctx, inputs)
            inputs = replace(
                inputs,
                evidence={
                    rule: tuple(ref for ref in refs if self.admits(rule, ref, pin))
                    for rule, refs in inputs.evidence.items()
                },
            )
            if inputs.added_tests is not None and any(
                seq not in self.events
                for probe in inputs.added_tests
                for seq in (probe.before_seq, probe.after_seq)
            ):
                inputs = replace(inputs, added_tests=None)
            assessment = assess(inputs, PROFILE)
            grants = (
                self.ctx.grants.uses(self.ctx, assessment, wait.grant_measurement)
                if wait.grant_measurement is not None
                else ()
            )
            if not self.valid(wait):
                return
            await self.ctx.publish(assessment)
            wait.latest = assessment
            if wait.terminal_seq is None:
                wait.spend_pin = pin
            if not self.valid(wait):
                if self.valid(wait, check_main=False):
                    await self.report_terminal(
                        wait,
                        "main_moved",
                        "Main moved during rules evaluation; review this change yourself.",
                        lambda: self.valid(wait, check_main=False),
                    )
                return
            if any(rule.result == "fail" for rule in assessment.rules):
                self.invalidate(wait.rec)
            elif unavailable:
                await self.terminal(wait, unavailable)
            elif all(
                rule.result == "pass" or covered_rule(assessment, rule, grants)
                for rule in assessment.rules
                if rule.rule != "combined_tests"
            ):
                failure = (self.ctx.ports.rules_admission or closed_admission)(wait.rec, assessment)
                if failure:
                    await self.report_terminal(wait, *failure, lambda: self.valid(wait))
