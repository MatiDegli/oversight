"""The closed catalog of committed plan events that can wake a lead."""

from collections.abc import Mapping

from oversight_contracts.events import AgentStatus, ServerEvent
from oversight_contracts.plans import Plan


def is_lead_wake_trigger(prior: Mapping[int, ServerEvent], seq: int, lead_id: str) -> bool:
    """Attribute one committed event using only state strictly before its seq.

    The supplied committed prefix includes the candidate at seq. Mapping insertion order and
    events after the candidate cannot change its attribution. A terminal transition triggers
    at most once per plan, even if a later revision leaves and re-enters a terminal state.
    """
    event = prior.get(seq)
    if event is None or event.type not in {"tests.executed", "agent.status", "plan.updated"}:
        return False

    plans: dict[str, Plan] = {}
    agents: dict[str, AgentStatus] = {}
    terminal: set[str] = set()
    for earlier in sorted(prior.values(), key=lambda row: row.seq):
        if earlier.seq >= seq:
            break
        if earlier.type == "plan.updated":
            plan = earlier.payload
            plans[plan.plan_id] = plan
            if plan.state in {"completed", "failed"}:
                terminal.add(plan.plan_id)
        elif earlier.type == "agent.status":
            agents[earlier.payload.agent_id] = earlier.payload

    plan_id: str | None
    if event.type == "tests.executed":
        execution = event.payload
        if execution.phase != "combined" or execution.suite.outcome == "passed":
            return False
        status = agents.get(execution.agent_id)
        plan_id = status.plan_id if status is not None else None
    elif event.type == "agent.status":
        if event.payload.state != "halted":
            return False
        plan_id = event.payload.plan_id
    elif event.type == "plan.updated":
        plan = event.payload
        if plan.state not in {"completed", "failed"} or plan.plan_id in terminal:
            return False
        plan_id = plan.plan_id
    else:
        return False

    owner = plans.get(plan_id) if plan_id is not None else None
    return owner is not None and owner.started_by == lead_id
