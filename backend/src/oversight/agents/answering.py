"""Pure projection of inbox origins from a confirmed, single-run event log.

Cards are supported here for the future producer's integration tests. Runtime admission keeps
that producer closed separately. No task status can resolve an originating entry.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from oversight.events import parse_needs_you
from oversight_contracts.decisions import ChangeDecisionRef, DecisionRef, ReviewCardDecisionRef
from oversight_contracts.events import ServerEvent
from oversight_contracts.review import ChangeFacts, ReviewCard

# Availability failures only. Integrity violations, failed suites, Git failures, spending and
# round limits describe other conditions. Unknown intermediate assessments are not entries.
CHECKS_UNAVAILABLE_CODES = frozenset(
    {
        "policy_invalid",
        "isolation_unavailable",
        "reviewer_missing",
        "reviewer_same",
        "reviewer_credentials",
        "author_halted",
        "review_timeout",
        "review_invalid",
        "review_truncated",
        "review_empty",
        "review_api_failed",
    }
)
UNAVAILABLE_PRODUCERS = {
    "review_card": "review-card producer unavailable",
    "follow_main": "follow-main producer unavailable",
    "risky_area": "risky-area producer unavailable",
    "lead_plan": "lead-plan producer unavailable; pending-plan approval signal unavailable",
    "examples_before_code": "examples-before-code producer unavailable",
}


class AnsweringError(ValueError):
    def __init__(self, code: Literal["invalid_request", "wrong_state"], message: str) -> None:
        super().__init__(message)
        self.code: Literal["invalid_request", "wrong_state"] = code


@dataclass(frozen=True)
class _Candidate:
    agent_id: str
    revision: int
    base: str
    head: str


def _key(ref: DecisionRef) -> tuple[object, ...]:
    if ref.kind == "lead_plan":
        return ref.kind, ref.agent_id, ref.revision
    if ref.kind == "review_card":
        return ref.kind, ref.change_id, ref.revision, ref.node_id, ref.question
    return ref.kind, ref.change_id, ref.revision


def _measurement(card: ReviewCard, facts: ChangeFacts, evidence: ServerEvent | None) -> bool:
    if evidence is None or evidence.type != "change.facts_recorded":
        return False
    source = evidence.payload
    if (
        source.change_id,
        source.revision,
        source.base_commit,
        source.head_commit,
        source.main_commit,
        source.candidate_tree,
    ) != (
        facts.change_id,
        facts.revision,
        facts.base_commit,
        facts.head_commit,
        facts.main_commit,
        facts.candidate_tree,
    ):
        return False
    return any(
        node.node_id == card.node_id
        and node.differing_count > 0
        and (card.example is None or card.example in node.rows)
        for node in [
            *(source.behavior_diff or []),
            *(node for node in source.measured_cone or [] if node.outcome == "changed"),
        ]
    )


class _Projection:
    def __init__(self) -> None:
        self.candidates: dict[str, _Candidate] = {}
        self.closed: set[str] = set()
        self.active: list[DecisionRef] = []
        self.history: list[DecisionRef] = []
        self.prior: dict[int, ServerEvent] = {}
        self.duplicate_cards: dict[str, set[tuple[object, ...]]] = {}

    def clear(self, change_id: str, kind: str | None = None) -> None:
        if kind in (None, "review_card"):
            self.duplicate_cards.pop(change_id, None)
        self.active = [
            ref
            for ref in self.active
            if ref.kind == "lead_plan"
            or ref.change_id != change_id
            or (kind is not None and ref.kind != kind)
        ]

    def add(self, ref: DecisionRef) -> None:
        self.active.append(ref)
        self.history.append(ref)

    def apply(self, event: ServerEvent) -> None:
        if event.type == "agent.status":
            p = event.payload
            if (
                p.base_commit
                and p.change_id not in self.candidates
                and p.change_id not in self.closed
            ):
                self.candidates[p.change_id] = _Candidate(
                    p.agent_id, 0, p.base_commit, p.base_commit
                )
        elif event.type == "change.proposed":
            p = event.payload
            self.clear(p.change_id)
            self.candidates[p.change_id] = _Candidate(
                p.agent_id, p.revision, p.base_commit, p.head_commit
            )
        elif event.type == "change.resolved":
            self.clear(event.payload.change_id)
            self.candidates.pop(event.payload.change_id, None)
            self.closed.add(event.payload.change_id)
        elif event.type == "change.updated":
            self.clear(event.payload.change_id)
        elif event.type == "error":
            self.notice(event)
        elif event.type == "change.facts_recorded":
            self.cards(event.payload)
        self.prior[event.seq] = event

    def notice(self, event: ServerEvent) -> None:
        assert event.type == "error"
        p, note = event.payload, parse_needs_you(event.payload.message)
        candidate = self.candidates.get(p.change_id or "")
        if (
            note is None
            or note.code not in CHECKS_UNAVAILABLE_CODES
            or candidate is None
            or p.source != "server"
            or p.agent_id != candidate.agent_id
            or (note.revision, note.head) != (candidate.revision, candidate.head)
        ):
            return
        assert p.change_id is not None
        ref = ChangeDecisionRef(
            kind="checks_unavailable",
            change_id=p.change_id,
            revision=note.revision,
            head_commit=note.head,
            evidence_seq=event.seq,
        )
        # Several unavailable checks on one candidate are one entry, raised by the first notice.
        if not any(_key(entry) == _key(ref) for entry in self.active):
            self.add(ref)

    def cards(self, facts: ChangeFacts) -> None:
        c = self.candidates.get(facts.change_id)
        if c is None or (facts.revision, facts.base_commit, facts.head_commit) != (
            c.revision,
            c.base,
            c.head,
        ):
            return
        self.clear(facts.change_id, "review_card")
        seen: set[tuple[object, ...]] = set()
        for card in facts.review_cards or []:
            key = ("review_card", facts.change_id, facts.revision, card.node_id, card.question)
            if key in seen:
                self.duplicate_cards.setdefault(facts.change_id, set()).add(key)
            seen.add(key)
            if card.answer is None and _measurement(card, facts, self.prior.get(card.evidence_seq)):
                self.add(
                    ReviewCardDecisionRef(
                        kind="review_card",
                        change_id=facts.change_id,
                        revision=facts.revision,
                        head_commit=facts.head_commit,
                        evidence_seq=card.evidence_seq,
                        node_id=card.node_id,
                        question=card.question,
                    )
                )


def validate_answering(
    reference: DecisionRef | None, events: Sequence[ServerEvent], *, run_id: str
) -> None:
    """Validate intrinsic identity and current availability without effects or IO.

    The caller owns the committed-log boundary. A complete log must start with this run's
    run.started; rejected or future envelopes must never be supplied as confirmed evidence.
    """
    if reference is None:
        return
    if (
        not events
        or events[0].type != "run.started"
        or events[0].payload.run_id != run_id
        or any(e.type == "run.started" and e.payload.run_id != run_id for e in events)
    ):
        raise AnsweringError("invalid_request", "answering evidence belongs to another run")
    if reference.kind not in {"checks_unavailable", "review_card"}:
        raise AnsweringError("wrong_state", UNAVAILABLE_PRODUCERS[reference.kind])
    projection = _Projection()
    for event in events:
        if event.seq in projection.prior or event.seq != len(projection.prior) + 1:
            raise AnsweringError(
                "invalid_request", "answering log must be chronological and unique"
            )
        projection.apply(event)
    if reference.evidence_seq not in projection.prior:
        raise AnsweringError("invalid_request", "answering evidence_seq is absent or future")
    matches = [ref for ref in projection.active if _key(ref) == _key(reference)]
    duplicates = {key for keys in projection.duplicate_cards.values() for key in keys}
    if len(matches) > 1 or _key(reference) in duplicates:
        raise AnsweringError("invalid_request", "answering entry is not unique")
    if matches == [reference]:
        return
    if reference in projection.history:
        raise AnsweringError("wrong_state", "answering entry is closed or replaced")
    raise AnsweringError("invalid_request", "answering identity has no matching confirmed entry")
