import type { AgentStatus, Plan, Slot } from "@contracts";
import type { State } from "../state";
import { costText, matchesNeedsYou } from "../state";
import { selectAgentCard } from "./selectors";
export { slotUsage as chatAgentCount } from "./taskBoxModel";

const hues: Slot[] = ["A", "B", "C", "D", "E", "F"];
const labels: Record<AgentStatus["state"], string> = {
  starting: "Starting",
  working: "Working",
  ready_for_review: "Ready for review",
  revising: "Revising",
  paused: "Paused",
  halted: "Halted",
  stopped: "Stopped",
  done: "Done",
};
type Identity = { team: string; hue: Slot; letter: string; order: number; member: number };

/** Reconstruct identities from the run-local prefix, independent of slots and render order. */
function teamOrigins(events: State["decisionEvents"]) {
  const plans = new Map<string, Plan>();
  const origins = new Map<string, AgentStatus>();
  for (const event of events) {
    if (event.type === "plan.updated") {
      const plan = event.payload;
      if (plan.revision > (plans.get(plan.plan_id)?.revision ?? 0)) plans.set(plan.plan_id, plan);
    }
    if (event.type === "agent.status") {
      const agent = event.payload,
        first = origins.get(agent.agent_id);
      origins.set(
        agent.agent_id,
        first
          ? {
              ...first,
              plan_id: first.plan_id ?? agent.plan_id,
              started_by: first.started_by ?? agent.started_by,
            }
          : agent,
      );
    }
  }
  return { plans, origins };
}

export function teamIdentities(state: State): Map<string, Identity> {
  const events = [...state.decisionEvents].sort((a, b) => a.seq - b.seq);
  const { plans, origins } = teamOrigins(events);
  const identities = new Map<string, Identity>();
  const teams = new Map<
    string,
    { hue: Slot; order: number; members: number; active: Set<string> }
  >();
  const idle = new Map(hues.map((hue) => [hue, 0]));
  const changes = new Map<string, boolean>();
  for (const event of events) {
    if (event.type === "agent.status" && !identities.has(event.payload.agent_id)) {
      const agent = origins.get(event.payload.agent_id);
      if (!agent) continue;
      const lead =
        agent.started_by ?? (agent.plan_id ? plans.get(agent.plan_id)?.started_by : null);
      const key = lead
        ? `lead:${lead}`
        : agent.plan_id
          ? `plan:${agent.plan_id}`
          : `agent:${agent.agent_id}`;
      let team = teams.get(key);
      if (!team) {
        const occupied = new Set(
          [...teams.values()].filter((t) => t.active.size).map((t) => t.hue),
        );
        const available = hues.filter((hue) => !occupied.has(hue));
        const hue = (available.length ? available : hues).reduce((a, b) =>
          (idle.get(a) ?? 0) <= (idle.get(b) ?? 0) ? a : b,
        );
        team = { hue, order: event.seq, members: 0, active: new Set() };
        teams.set(key, team);
      }
      const member = ++team.members;
      identities.set(agent.agent_id, {
        team: key,
        hue: team.hue,
        order: team.order,
        member,
        letter: agent.plan_id || lead ? `${team.hue}·${String(member)}` : team.hue,
      });
    }
    if (event.type === "change.proposed") changes.set(event.payload.change_id, true);
    if (event.type === "change.resolved") {
      const r = event.payload;
      changes.set(r.change_id, r.merge === "conflict" || r.decision === "changes_requested");
    }
    if (
      event.type !== "agent.status" &&
      event.type !== "change.resolved" &&
      event.type !== "change.proposed"
    )
      continue;
    const id = event.payload.agent_id,
      identity = identities.get(id);
    if (!identity) continue;
    const team = teams.get(identity.team);
    if (!team) continue;
    const status = event.type === "agent.status" ? event.payload : undefined;
    const held =
      changes.get(event.payload.change_id) ??
      (status?.state !== "done" && status?.state !== "stopped");
    if (held) team.active.add(id);
    else if (team.active.delete(id) && !team.active.size) idle.set(team.hue, event.seq);
  }
  return identities;
}

/** Row facts reuse the card's tool, model-wait, step-limit and cumulative cost projections. */
export function selectChatAgents(state: State) {
  return [...teamIdentities(state)]
    .sort(([, a], [, b]) => a.order - b.order || a.member - b.member)
    .flatMap(([id, identity]) => {
      const card = selectAgentCard(state, id);
      if (!card) return [];
      const { agent } = card,
        change = state.changes[agent.change_id];
      const resolution = change?.resolution;
      const merged = resolution?.decision === "approved" && resolution.merge === "merged";
      const note =
        change && matchesNeedsYou(change.needsYou, card.proposal)
          ? change.needsYou?.reason
          : undefined;
      const label =
        agent.state === "working"
          ? card.activityLabel !== "Waiting" && !card.steps.length && state.focus[id]
            ? state.focus[id].mode === "edit"
              ? "Editing"
              : "Reading"
            : card.activityLabel
          : labels[agent.state];
      const activity = merged
        ? resolution.decided_by === "rules"
          ? "Merged by your rules"
          : "Merged"
        : agent.state === "ready_for_review"
          ? (note ?? "Waiting for your review")
          : agent.state === "working" || agent.state === "revising"
            ? card.activity
            : (agent.detail ?? labels[agent.state]);
      return [
        {
          ...identity,
          id,
          task: agent.task,
          model: card.modelLabel,
          label,
          activity,
          ready: card.readyForReview,
          done: agent.state === "done" || agent.state === "stopped",
          step: Math.min(card.step, card.maxSteps),
          maxSteps: card.maxSteps,
          cost:
            card.cost === undefined
              ? undefined
              : card.cost === null
                ? "Cost unknown"
                : costText(card.cost),
        },
      ];
    });
}
