"""F2: measured questions with confirmed evidence, independent from cross-review authority."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Protocol

from pydantic import TypeAdapter

from oversight.agents import DecisionInput, DecisionOutcome, Publish
from oversight.changes._measured import capture_review_samples, project_measured
from oversight.changes._review_samples import Observation, Selection, select
from oversight.changes._summary import AskedFor, SummaryEvidence, build_summary
from oversight.events import EventPayload, StoredEvent
from oversight_contracts.events import ServerEvent, SuiteExecution
from oversight_contracts.review import ChangeFacts, ChangeTarget, NodeBehaviorDiff, ReviewCard

if TYPE_CHECKING:
    from oversight.changes._facts import FactsEvidence
    from oversight.changes._state import ChangeRecord
    from oversight.changes._support import ChangeContext

QUESTION = "Was this behavior change asked for?"
UNCERTAIN = "Oversight could not tell whether this was asked for"
PREFIX = "review cards: "
_EVENT = TypeAdapter(ServerEvent)


class DifferenceDecisions(Protocol):
    async def classify_difference(
        self, input: DecisionInput, *, deadline: float
    ) -> DecisionOutcome: ...


@dataclass(frozen=True)
class ReviewCardLimits:
    classifications: int = 8
    call_s: float = 30
    total_s: float = 120

    def __post_init__(self) -> None:
        if type(self.classifications) is not int or self.classifications < 0:
            raise ValueError("review card classifications must be a nonnegative integer")
        if any(not math.isfinite(value) or value <= 0 for value in (self.call_s, self.total_s)):
            raise ValueError("review card deadlines must be finite and positive")


def target_of(facts: ChangeFacts) -> ChangeTarget:
    return ChangeTarget.model_validate(facts.model_dump(include=set(ChangeTarget.model_fields)))


def selection(evidence: FactsEvidence, limit: int) -> Selection:
    samples: list[Observation] = []
    problems: list[str] = []
    for node in evidence.behavior_evidence.functions if evidence.behavior_evidence else ():
        direct = node.review_samples()
        samples.extend(direct.samples)
        problems.extend(direct.problems)
    if evidence.measured:
        samples.extend(capture_review_samples(evidence.measured))
        for node in project_measured(evidence.measured, evidence.target):
            if not node.complete:
                problems.append(f"{PREFIX}{node.node_id}: caller comparison incomplete")
            if node.differing_count > sum(not row.unstable for row in node.rows):
                problems.append(f"{PREFIX}{node.node_id}: differing observations not retained")
    result = select(tuple(samples), limit)
    return Selection(result.samples, tuple(dict.fromkeys((*problems, *result.problems))))


@dataclass(frozen=True)
class CardCapture:
    run_id: str
    target: ChangeTarget
    generation: int
    agent_id: str
    task: str
    selected: Selection
    direct_nodes: frozenset[str]
    executions: Mapping[int, SuiteExecution]


@dataclass
class CardJob:
    capture: CardCapture
    summary_evidence: SummaryEvidence
    evidence: StoredEvent | None = None
    latest: StoredEvent | None = None
    facts: ChangeFacts | None = None
    task: asyncio.Task[None] | None = None
    deadline: float | None = None
    consumed: int = 0
    cards: list[ReviewCard] = field(default_factory=list[ReviewCard])
    asked_for: list[AskedFor] = field(default_factory=list[AskedFor])
    problems: list[str] = field(default_factory=list[str])
    status: str = "pending"

    @property
    def preparing(self) -> bool:
        """Classification is not settled until its final publication returns a receipt."""
        return self.status == "pending" or (self.task is not None and not self.task.done())


class ReviewCards:
    """One run-owned producer. Only committed receipts enter its evidence registry.

    All change publications share a short publication lock. Cancellation drains an admitted
    publish before an edit/resolution can publish, so replay sees that update before the
    invalidating event. Provider work holds neither this lock nor a Git lock.
    """

    def __init__(
        self,
        ctx: ChangeContext,
        run_id: str,
        decisions: DifferenceDecisions,
        limits: ReviewCardLimits,
        publish: Publish,
    ) -> None:
        self.ctx, self.run_id, self.decisions = ctx, run_id, decisions
        self.limits, self._publish = limits, publish
        self.jobs: dict[str, CardJob] = {}
        self.events: dict[int, ServerEvent] = {}
        self.lock = asyncio.Lock()
        self.tasks: set[asyncio.Task[None]] = set()
        self.closed = False

    def prepare(self, rec: ChangeRecord, evidence: FactsEvidence) -> CardJob:
        key = evidence.target.model_dump_json()
        if key not in self.jobs:
            if any(
                (job.capture.target.change_id, job.capture.target.revision)
                == (evidence.target.change_id, evidence.target.revision)
                for job in self.jobs.values()
            ):
                raise ValueError("review card revision already captured a different target")
            selected = selection(evidence, self.limits.classifications)
            # A node may occur in both projections with different aggregate counts.
            # Retain which producer supplied its selected witness before classifying.
            self.jobs[key] = CardJob(
                CardCapture(
                    self.run_id,
                    evidence.target.model_copy(deep=True),
                    rec.card_generation,
                    rec.agent_id,
                    rec.task.task,
                    selected,
                    frozenset(
                        sample.node_id
                        for sample in selected.samples
                        for node in (
                            evidence.behavior_evidence.functions
                            if evidence.behavior_evidence
                            else ()
                        )
                        if any(row.observation == sample for row in node.rows)
                    ),
                    {seq: run.model_copy(deep=True) for seq, run in evidence.executions.items()},
                ),
                evidence.summary_evidence,
            )
        return self.jobs[key]

    def current(self, capture: CardCapture) -> bool:
        rec = self.ctx.state.changes.get(capture.target.change_id)
        return bool(
            not self.closed
            and not self.ctx.state.closing
            and capture.run_id == self.run_id
            and rec is not None
            and self.ctx.state.agents.get(rec.agent_id) is rec
            and rec.card_generation == capture.generation
            and not rec.delivery_rejected
            and rec.state in {"working", "ready"}
            and (rec.revision, rec.head_commit, rec.base_commit)
            == (
                capture.target.revision,
                capture.target.head_commit,
                capture.target.base_commit,
            )
        )

    def for_change(self, rec: ChangeRecord) -> CardJob | None:
        """Find the observed proposal, including after a failed decision invalidated its worker.

        A revision captures only one exact target (enforced by prepare). Published questions
        survive worker cancellation; a later revision or head must never inherit them.
        """
        return next(
            (
                job
                for job in self.jobs.values()
                if job.capture.run_id == self.run_id
                and (
                    job.capture.target.change_id,
                    job.capture.agent_id,
                    job.capture.target.revision,
                    job.capture.target.base_commit,
                    job.capture.target.head_commit,
                )
                == (rec.change_id, rec.agent_id, rec.revision, rec.base_commit, rec.head_commit)
            ),
            None,
        )

    def diagnostics(self, job: CardJob) -> list[str]:
        return [
            *job.capture.selected.problems,
            *job.problems,
            f"{PREFIX}{job.status}; {job.consumed}/{len(job.capture.selected.samples)} classified",
        ]

    async def publish(self, payload: EventPayload) -> object:
        async with self.lock:
            if not isinstance(payload, ChangeFacts):
                return await self.ctx.whole(self._forward(payload))
            job = self.jobs.get(target_of(payload).model_dump_json())
            if job is None:
                return await self.ctx.whole(self._forward(payload))
            if not self.current(job.capture):
                raise ValueError("review card candidate is no longer current")
            # Re-publishing measurements must not reset selection, calls or later producer data.
            if job.latest is not None:
                excluded = {"review_cards", "problems", "summary"}
                if job.facts is None or payload.model_dump(
                    exclude=excluded
                ) != job.facts.model_dump(exclude=excluded):
                    raise ValueError("republished measurements differ from committed facts")
                if job.status == "failed":
                    job.status = (
                        "finished"
                        if job.consumed == len(job.capture.selected.samples)
                        else "truncated"
                    )
                    try:
                        return await self._publish_result(job)
                    except BaseException:
                        job.status = "failed"
                        raise
                return job.latest
            job.status = "pending"
            facts = payload.model_copy(
                deep=True,
                update={
                    "review_cards": [],
                    "problems": [*payload.problems, *self.diagnostics(job)],
                },
            )
            try:
                receipt = await self._emit(job, facts)
            except BaseException:
                job.status = "failed"
                raise
            job.evidence = receipt
            job.deadline = asyncio.get_running_loop().time() + self.limits.total_s
            job.task = asyncio.create_task(
                self._run(job), name=f"cards-{facts.change_id}-{facts.revision}"
            )
            self.tasks.add(job.task)
            job.task.add_done_callback(self.tasks.discard)
            rec = self.ctx.state.changes[facts.change_id]
            rec.card_task = job.task
            return receipt

    async def _forward(self, payload: EventPayload) -> object:
        return await self._publish(payload)

    async def _emit(
        self, job: CardJob, facts: ChangeFacts, summary: SummaryEvidence | None = None
    ) -> StoredEvent:
        return await self.ctx.whole(self._commit(job, facts, summary))

    async def _commit(
        self, job: CardJob, facts: ChangeFacts, summary: SummaryEvidence | None
    ) -> StoredEvent:
        frozen = ChangeFacts.model_validate_json(facts.model_dump_json())
        captured = summary if summary is not None else job.summary_evidence
        self.validate(job, frozen, summary=captured)
        receipt = await self._publish(frozen)
        if not isinstance(receipt, StoredEvent):
            raise ValueError("review cards require a confirmed StoredEvent receipt")
        event = _EVENT.validate_json(receipt.envelope)
        if (
            event.type != "change.facts_recorded"
            or event.payload != frozen
            or event.seq != receipt.seq
        ):
            raise ValueError("review card receipt differs from published facts")
        self.events[event.seq] = event
        job.facts, job.latest = event.payload, receipt
        job.summary_evidence = captured
        return receipt

    def validate(
        self, job: CardJob, facts: ChangeFacts, *, summary: SummaryEvidence | None = None
    ) -> None:
        if target_of(facts) != job.capture.target or job.capture.run_id != self.run_id:
            raise ValueError("review cards belong to another target or run")
        if facts.summary != build_summary(
            summary if summary is not None else job.summary_evidence,
            job.capture.executions,
            revision=job.capture.target.revision,
        ):
            raise ValueError("review card summary differs from captured evidence")
        identities: set[tuple[str, str]] = set()
        witnesses: set[Observation] = set()
        eligible = {
            (sample.node_id, sample.question(base)): sample
            for sample in job.capture.selected.samples
            for base in (QUESTION, UNCERTAIN)
        }
        for card in facts.review_cards or []:
            sample = eligible.get((card.node_id, card.question))
            event = self.events.get(card.evidence_seq)
            if (
                job.evidence is None
                or card.evidence_seq != job.evidence.seq
                or event is None
                or event.type != "change.facts_recorded"
                or target_of(event.payload) != job.capture.target
                or card.example is None
                or card.example.unstable
                or sample is None
                or sample.example() != card.example
            ):
                raise ValueError("review card lacks eligible prior evidence")
            sources = [*(event.payload.behavior_diff or []), *(event.payload.measured_cone or [])]
            if not any(
                node.node_id == card.node_id and card.example in node.rows for node in sources
            ):
                raise ValueError("review card example is absent from evidence")
            identity = card.node_id, card.question
            if identity in identities or sample in witnesses:
                raise ValueError("review card identity is invalid or duplicated")
            identities.add(identity)
            witnesses.add(sample)
        if len(identities) > 8:
            raise ValueError("review cards exceed the contractual limit")

    async def update(self, target: ChangeTarget, updates: Mapping[str, object]) -> StoredEvent:
        """Patch the latest full facts, so independent producers never restore an old copy."""
        async with self.lock:
            job = self.jobs[target.model_dump_json()]
            if not self.current(job.capture) or job.facts is None:
                raise ValueError("review card candidate is no longer current")
            if set(updates) & set(ChangeTarget.model_fields):
                raise ValueError("facts updates cannot change candidate identity")
            facts = ChangeFacts.model_validate(job.facts.model_dump() | dict(updates))
            return await self._emit(job, facts)

    async def _finish(self, job: CardJob) -> None:
        async with self.lock:
            if not self.current(job.capture) or job.facts is None:
                return
            await self._publish_result(job)

    async def _publish_result(self, job: CardJob) -> StoredEvent:
        summary = replace(
            job.summary_evidence,
            asked_for=tuple(job.asked_for),
            questions=len(job.cards) if job.cards or job.asked_for else None,
        )
        return await self._emit(job, self._result_facts(job, summary), summary)

    def _result_facts(self, job: CardJob, summary: SummaryEvidence) -> ChangeFacts:
        assert job.facts is not None
        previous = {(c.node_id, c.question): c for c in job.facts.review_cards or []}
        cards: list[ReviewCard] = []
        for card in job.cards:
            old = previous.get((card.node_id, card.question))
            cards.append(old if old is not None and old.example == card.example else card)
        problems = [p for p in job.facts.problems if not p.startswith(PREFIX)]
        return job.facts.model_copy(
            deep=True,
            update={
                "review_cards": cards,
                "summary": build_summary(
                    summary, job.capture.executions, revision=job.capture.target.revision
                ),
                "problems": [*problems, *self.diagnostics(job)],
            },
        )

    def _asked_for(self, job: CardJob, sample: Observation) -> AskedFor:
        assert job.evidence is not None
        event = self.events[job.evidence.seq]
        assert event.type == "change.facts_recorded"
        sources = (
            event.payload.behavior_diff
            if sample.node_id in job.capture.direct_nodes
            else event.payload.measured_cone
        )
        for node in sources or []:
            if node.node_id == sample.node_id and sample.example() in node.rows:
                return AskedFor(
                    node.node_id,
                    sample.coverage if sample.group is not None else node.differing_count,
                    node.compared_count,
                )
        raise ValueError("asked-for sample is absent from published evidence")

    def _record_asked_for(self, job: CardJob, sample: Observation) -> None:
        asked = self._asked_for(job, sample)
        for index, previous in enumerate(job.asked_for):
            if previous.node_id == asked.node_id:
                job.asked_for[index] = replace(
                    asked, differing_count=previous.differing_count + asked.differing_count
                )
                return
        job.asked_for.append(asked)

    async def _run(self, job: CardJob) -> None:
        try:
            assert job.evidence is not None and job.deadline is not None
            for sample in job.capture.selected.samples:
                if not self.current(job.capture):
                    return
                now = asyncio.get_running_loop().time()
                if now >= job.deadline:
                    job.status = "truncated"
                    remaining = sum(
                        item.coverage for item in job.capture.selected.samples[job.consumed :]
                    )
                    job.problems.append(
                        f"{PREFIX}review deadline exhausted; remaining nodes unclassified; "
                        f"{remaining} differing inputs unclassified"
                    )
                    break
                job.consumed += 1
                input = DecisionInput(
                    job.capture.target,
                    job.capture.agent_id,
                    job.capture.task,
                    NodeBehaviorDiff(
                        node_id=sample.node_id,
                        compared_count=1,
                        differing_count=1,
                        rows=[sample.example()],
                    ),
                )
                deadline = min(job.deadline, now + self.limits.call_s)
                try:
                    async with asyncio.timeout_at(deadline):
                        outcome = await self.decisions.classify_difference(input, deadline=deadline)
                except TimeoutError:
                    job.problems.append(sample.problem("deadline"))
                    continue
                except Exception as error:
                    job.problems.append(sample.problem(f"unavailable ({type(error).__name__})"))
                    continue
                if not self.current(job.capture):
                    return
                if asyncio.get_running_loop().time() >= deadline:
                    job.problems.append(sample.problem("deadline"))
                elif outcome.target != input.target or outcome.node_id != sample.node_id:
                    job.problems.append(sample.problem("classification identity mismatch"))
                elif outcome.choice is None:
                    job.problems.append(sample.problem(f"unavailable ({outcome.unavailable})"))
                elif outcome.choice == "asked_for":
                    self._record_asked_for(job, sample)
                elif outcome.choice in {"not_asked_for", "cannot_tell"}:
                    if len(job.cards) == 8:
                        job.problems.append(sample.problem("contractual card limit"))
                    else:
                        job.cards.append(
                            ReviewCard(
                                node_id=sample.node_id,
                                question=sample.question(
                                    UNCERTAIN if outcome.choice == "cannot_tell" else QUESTION
                                ),
                                evidence_seq=job.evidence.seq,
                                example=sample.example(),
                            )
                        )
            if job.status == "pending":
                job.status = "finished"
            await self._finish(job)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            job.status = "failed"
            job.problems.append(
                f"{PREFIX}publication or classification unavailable ({type(error).__name__})"
            )
            # No producer retries: a subsequent explicit publication can expose this diagnostic.

    async def close(self) -> None:
        self.closed = True
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
