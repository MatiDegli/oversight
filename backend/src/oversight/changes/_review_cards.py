"""F2: measured questions with confirmed evidence, independent from cross-review authority."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from pydantic import TypeAdapter

from oversight.agents import DecisionInput, DecisionOutcome, Publish
from oversight.changes._measured import capture_review_samples, project_measured
from oversight.changes._review_samples import Observation, Selection, select
from oversight.events import EventPayload, StoredEvent
from oversight_contracts.events import ServerEvent
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
        for row in node.rows:
            if row.observation is not None:
                samples.append(row.observation)
            else:
                problems.append(f"{PREFIX}{node.node_id}: row witness unavailable")
        if node.not_probed:
            problems.append(f"{PREFIX}{node.node_id}: behavior comparison incomplete")
        if node.differing > sum(not row.unstable for row in node.rows):
            problems.append(f"{PREFIX}{node.node_id}: differing observations not retained")
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


@dataclass
class CardJob:
    capture: CardCapture
    evidence: StoredEvent | None = None
    latest: StoredEvent | None = None
    facts: ChangeFacts | None = None
    task: asyncio.Task[None] | None = None
    deadline: float | None = None
    consumed: int = 0
    cards: list[ReviewCard] = field(default_factory=list[ReviewCard])
    problems: list[str] = field(default_factory=list[str])
    status: str = "pending"


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
            self.jobs[key] = CardJob(
                CardCapture(
                    self.run_id,
                    evidence.target.model_copy(deep=True),
                    rec.card_generation,
                    rec.agent_id,
                    rec.task.task,
                    selection(evidence, self.limits.classifications),
                )
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
                if job.status == "failed" and job.facts is not None:
                    job.status = (
                        "finished"
                        if job.consumed == len(job.capture.selected.samples)
                        else "truncated"
                    )
                    try:
                        return await self._emit(job, self._result_facts(job))
                    except BaseException:
                        job.status = "failed"
                        raise
                return job.latest
            facts = payload.model_copy(
                deep=True,
                update={
                    "review_cards": [],
                    "problems": [*payload.problems, *self.diagnostics(job)],
                },
            )
            receipt = await self._emit(job, facts)
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

    async def _emit(self, job: CardJob, facts: ChangeFacts) -> StoredEvent:
        return await self.ctx.whole(self._commit(job, facts))

    async def _commit(self, job: CardJob, facts: ChangeFacts) -> StoredEvent:
        frozen = ChangeFacts.model_validate_json(facts.model_dump_json())
        if frozen.review_cards:
            self.validate(job, frozen)
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
        return receipt

    def validate(self, job: CardJob, facts: ChangeFacts) -> None:
        if target_of(facts) != job.capture.target or job.capture.run_id != self.run_id:
            raise ValueError("review cards belong to another target or run")
        identities: set[tuple[str, str]] = set()
        eligible = {sample.node_id: sample.example() for sample in job.capture.selected.samples}
        for card in facts.review_cards or []:
            event = self.events.get(card.evidence_seq)
            if (
                job.evidence is None
                or card.evidence_seq != job.evidence.seq
                or event is None
                or event.type != "change.facts_recorded"
                or target_of(event.payload) != job.capture.target
                or card.example is None
                or card.example.unstable
                or eligible.get(card.node_id) != card.example
            ):
                raise ValueError("review card lacks eligible prior evidence")
            sources = [*(event.payload.behavior_diff or []), *(event.payload.measured_cone or [])]
            if not any(
                node.node_id == card.node_id and card.example in node.rows for node in sources
            ):
                raise ValueError("review card example is absent from evidence")
            identity = card.node_id, card.question
            if identity in identities or card.question not in {QUESTION, UNCERTAIN}:
                raise ValueError("review card identity is invalid or duplicated")
            identities.add(identity)
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
            await self._emit(job, self._result_facts(job))

    def _result_facts(self, job: CardJob) -> ChangeFacts:
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
                "problems": [*problems, *self.diagnostics(job)],
            },
        )

    async def _run(self, job: CardJob) -> None:
        try:
            assert job.evidence is not None and job.deadline is not None
            for sample in job.capture.selected.samples:
                if not self.current(job.capture):
                    return
                now = asyncio.get_running_loop().time()
                if now >= job.deadline:
                    job.status = "truncated"
                    job.problems.append(
                        f"{PREFIX}review deadline exhausted; remaining nodes unclassified"
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
                    job.problems.append(f"{PREFIX}{sample.node_id}: deadline")
                    continue
                except Exception as error:
                    job.problems.append(
                        f"{PREFIX}{sample.node_id}: unavailable ({type(error).__name__})"
                    )
                    continue
                if not self.current(job.capture):
                    return
                if asyncio.get_running_loop().time() >= deadline:
                    job.problems.append(f"{PREFIX}{sample.node_id}: deadline")
                elif outcome.target != input.target or outcome.node_id != sample.node_id:
                    job.problems.append(
                        f"{PREFIX}{sample.node_id}: classification identity mismatch"
                    )
                elif outcome.choice is None:
                    job.problems.append(
                        f"{PREFIX}{sample.node_id}: unavailable ({outcome.unavailable})"
                    )
                elif outcome.choice in {"not_asked_for", "cannot_tell"}:
                    if len(job.cards) == 8:
                        job.problems.append(f"{PREFIX}{sample.node_id}: contractual card limit")
                    else:
                        job.cards.append(
                            ReviewCard(
                                node_id=sample.node_id,
                                question=UNCERTAIN if outcome.choice == "cannot_tell" else QUESTION,
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
