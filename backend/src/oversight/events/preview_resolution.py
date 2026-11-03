"""Preview-only committed-fact membership; no launch authority or model inventory."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from oversight_contracts.events import AgentStatus, ChangeProposed, ServerEvent
from oversight_contracts.previews import PreviewStatus, StartPreview


@dataclass
class PreviewProjection:
    run_id: str | None = None
    leads: set[str] = field(default_factory=set[str])
    agents: dict[str, AgentStatus] = field(default_factory=dict[str, AgentStatus])
    proposals: dict[str, ChangeProposed] = field(default_factory=dict[str, ChangeProposed])
    pinned: set[tuple[str, str, int, str]] = field(default_factory=set[tuple[str, str, int, str]])

    def candidate(self, agent_id: str) -> ChangeProposed | None:
        agent = self.agents.get(agent_id)
        proposal = self.proposals.get(agent.change_id) if agent else None
        if (
            agent is None
            or self.run_id is None
            or agent.state != "ready_for_review"
            or proposal is None
            or proposal.agent_id != agent_id
            or proposal.change_id != agent.change_id
        ):
            return None
        try:
            StartPreview(
                run_id=self.run_id,
                agent_id=agent_id,
                revision=proposal.revision,
                head_commit=proposal.head_commit,
            )
        except ValueError:
            return None
        return proposal


def project_previews(events: Iterable[ServerEvent]) -> PreviewProjection:
    result = PreviewProjection()
    for event in events:
        match event.type:
            case "run.started":
                result = PreviewProjection(run_id=event.payload.run_id)
            case "agent.status":
                result.agents[event.payload.agent_id] = event.payload
            case "lead.status":
                result.leads.add(event.payload.agent_id)
            case "change.proposed":
                p = event.payload
                result.proposals[p.change_id] = p
                if (
                    p.agent_id in result.agents
                    and result.agents[p.agent_id].change_id == p.change_id
                ):
                    result.pinned.add((p.agent_id, p.change_id, p.revision, p.head_commit))
            case "change.resolved":
                p = event.payload
                # A refused combined merge leaves the current proposal unresolved.
                if p.merge != "conflict":
                    result.proposals.pop(p.change_id, None)
            case "change.updated":
                # Revision work invalidates an earlier proposal even before another finish.
                result.proposals.pop(event.payload.change_id, None)
            case _:
                pass
    return result


def validate_preview_status(status: PreviewStatus, prior: Mapping[int, ServerEvent]) -> None:
    projection = project_previews(prior.values())
    identity = (status.agent_id, status.change_id, status.revision, status.head_commit)
    if status.run_id != projection.run_id or identity not in projection.pinned:
        raise ValueError("preview requires run, coding agent and pinned proposal membership")
    receipts = [
        e.payload
        for e in prior.values()
        if e.type == "preview.status" and e.payload.preview_id == status.preview_id
    ]
    if status.state == "starting":
        if receipts:
            raise ValueError("preview identity already exists")
    elif not receipts or any(
        (p.run_id, p.agent_id, p.change_id, p.revision, p.head_commit) != (status.run_id, *identity)
        for p in receipts
    ):
        raise ValueError("preview status differs from its pinned creation")
    if status.state in {"starting", "ready"}:
        current = projection.candidate(status.agent_id)
        if (
            current is None
            or (current.change_id, current.revision, current.head_commit) != identity[1:]
        ):
            raise ValueError("preview requires the current unresolved coding proposal")


def validate_preview_view(
    agent_id: str,
    committed: Mapping[int, ServerEvent],
    through_seq: int,
    lead_id: str,
    prior: Mapping[int, ServerEvent],
) -> None:
    observed = project_previews(committed[seq] for seq in range(1, through_seq + 1))
    current = project_previews(prior.values())
    candidate = observed.candidate(agent_id)
    if (
        candidate is None
        or lead_id not in observed.leads
        or lead_id not in current.leads
        or candidate != current.candidate(agent_id)
        or observed.run_id != current.run_id
    ):
        raise ValueError("preview target is absent, resolved, stale or inconsistent")
