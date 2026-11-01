"""Archived read projections over one immutable inventory of committed envelopes."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from oversight.changes._state import ChangeNotFound
from oversight.events import EventStore, fold_stages, fold_usage
from oversight_contracts.commands import ChangeView
from oversight_contracts.events import (
    AgentStatus,
    ChangeProposed,
    ChangeResolved,
    FileDiff,
    ServerEvent,
)
from oversight_contracts.policy import ChangeAssessment
from oversight_contracts.review import ChangeFacts, CrossReview

type _Evidence = ChangeFacts | CrossReview | ChangeAssessment


@dataclass
class _Archive:
    found: bool = False
    proposal: ChangeProposed | None = None
    resolution: ChangeResolved | None = None
    resolution_seq: int = 0
    files: dict[str, FileDiff] = field(default_factory=dict[str, FileDiff])
    after: int = 0
    target_valid: bool = True
    main_commit: str | None = None

    def apply(self, change_id: str, event: ServerEvent) -> None:
        if event.type == "graph.snapshot" or event.type == "graph.delta":
            self.main_commit = event.payload.commit
        if getattr(event.payload, "change_id", None) != change_id:
            return
        # Errors and usage alone do not establish that a change exists.
        match event.type:
            case "agent.status" | "change.facts_recorded" | "change.reviewed" | "change.assessed":
                self.found = True
            case "change.updated":
                self.found = True
                self.files[event.payload.file.path] = event.payload.file
                self.after, self.target_valid = event.seq, False
            case "change.proposed":
                self.found = True
                self.proposal = event.payload
                self.after, self.target_valid = event.seq, True
            case "change.resolved":
                self.found = True
                self.resolution = event.payload
                self.resolution_seq = event.seq
                if event.payload.decision == "changes_requested":
                    self.after, self.target_valid = event.seq, False
            case _:
                pass


def _candidate_matches(evidence: _Evidence, proposal: ChangeProposed | None) -> bool:
    return proposal is None or (
        evidence.revision == proposal.revision
        and evidence.base_commit == proposal.base_commit
        and evidence.head_commit == proposal.head_commit
    )


def _target(evidence: _Evidence) -> tuple[str, int, str, str, str, str | None]:
    return (
        evidence.change_id,
        evidence.revision,
        evidence.base_commit,
        evidence.head_commit,
        evidence.main_commit,
        evidence.candidate_tree,
    )


def _evidence(
    change_id: str, events: Sequence[ServerEvent], archive: _Archive
) -> tuple[ChangeFacts | None, CrossReview | None, ChangeAssessment | None]:
    """Facts/assessments establish the target; a delayed advisory review cannot replace it."""
    if not archive.target_valid:
        return None, None, None
    terminal = archive.resolution is not None and archive.resolution.decision != "changes_requested"
    main_commit = None if terminal else archive.main_commit
    candidates: list[tuple[int, _Evidence]] = []
    for event in events:
        payload = event.payload
        if event.seq <= archive.after or not isinstance(
            payload, ChangeFacts | CrossReview | ChangeAssessment
        ):
            continue
        if payload.change_id != change_id or not _candidate_matches(payload, archive.proposal):
            continue
        if main_commit is not None and payload.main_commit != main_commit:
            continue
        candidates.append((event.seq, payload))
    if terminal and archive.resolution is not None:
        final = archive.resolution.assessment
        if final is not None and _candidate_matches(final, archive.proposal):
            candidates.append((archive.resolution_seq, final))
    targets = [p for seq, p in candidates if not terminal or seq <= archive.resolution_seq]
    authoritative = [p for p in targets if not isinstance(p, CrossReview)]
    if not targets:
        return None, None, None
    target = _target((authoritative or targets)[-1])
    facts = review = assessment = None
    for _, payload in candidates:
        if _target(payload) != target:
            continue
        if isinstance(payload, ChangeFacts):
            facts = payload
        elif isinstance(payload, CrossReview):
            review = payload
        else:
            assessment = payload
    return facts, review, assessment


def build_change_view(change_id: str, events: Sequence[ServerEvent]) -> ChangeView:
    """Project an ordered committed inventory, retaining unknown accounting on a partial log.

    Never pass a per-change filtered inventory: unrelated envelopes establish completeness too.
    File contents come exclusively from change.updated, including after worktree disposal.
    """
    archive = _Archive()
    for event in events:
        archive.apply(change_id, event)
    if not archive.found:
        raise ChangeNotFound(change_id)
    facts, review, assessment = _evidence(change_id, events, archive)
    usage = None
    rounds = None
    stages = None
    if all(event.seq == expected for expected, event in enumerate(events, start=1)):
        stages = fold_stages(events, change_id)
        rounds = sum(
            event.type == "change.resolved"
            and event.payload.change_id == change_id
            and event.payload.decision == "changes_requested"
            for event in events
        )
        try:
            accounting = fold_usage(events).by_change.get(change_id)
        except ValueError:
            accounting = None  # Invalid historical costs cannot become plausible totals.
        if accounting is not None and accounting.usage.by_role:
            usage = accounting.usage
    return ChangeView(
        change_id=change_id,
        seq=events[-1].seq if events else 0,
        proposal=archive.proposal,
        files=list(archive.files.values()),
        facts=facts,
        review=review,
        assessment=assessment,
        resolution=archive.resolution,
        usage=usage,
        revision_rounds=rounds,
        stages=stages,
    )


def read_change_view(store: EventStore, change_id: str) -> ChangeView:
    """Capture seq and contents together, without yielding to the writer between them."""
    return build_change_view(change_id, store.committed_events())


def read_agent_status(store: EventStore, agent_id: str) -> AgentStatus:
    """Latest committed coding-agent status; lead.status never qualifies."""
    for event in reversed(store.committed_events()):
        if event.type == "agent.status" and event.payload.agent_id == agent_id:
            return event.payload
    raise ChangeNotFound(agent_id)
