import type { AgentStatus, Slot } from "@contracts";
import type { State } from "../state";
import { costText, matchesNeedsYou } from "../state";
// eslint-disable-next-line no-restricted-imports -- K0's sole team selector has no barrel export; K2a may import it only.
import { selectTeams } from "../state/teamSelector";
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

type HueTeam = { hue: Slot; order: number; members: number; active: Set<string> };

function openTeam(teams: Map<string, HueTeam>, idle: Map<Slot, number>, key: string, seq: number) {
  let team = teams.get(key);
  if (!team) {
    const occupied = new Set([...teams.values()].filter((t) => t.active.size).map((t) => t.hue));
    const available = hues.filter((hue) => !occupied.has(hue));
    const hue = (available.length ? available : hues).reduce((a, b) =>
      (idle.get(a) ?? 0) <= (idle.get(b) ?? 0) ? a : b,
    );
    team = { hue, order: seq, members: 0, active: new Set() };
    teams.set(key, team);
  }
  return team;
}

/** Hue history follows first status events; membership comes only from selectTeams. */
export function teamIdentities(state: State): Map<string, Identity> {
  const membership = selectTeams(state).byAgent;
  const identities = new Map<string, Identity>();
  const teams = new Map<string, HueTeam>();
  const idle = new Map(hues.map((hue) => [hue, 0]));
  const changes = new Map<string, boolean>();
  const leadRevisions = new Map<string, number>();
  for (const event of state.decisionEvents) {
    if (event.type === "lead.status") {
      const lead = event.payload;
      if (lead.revision <= (leadRevisions.get(lead.agent_id) ?? 0)) continue;
      leadRevisions.set(lead.agent_id, lead.revision);
    }
    if (event.type === "lead.status" || event.type === "agent.status") {
      const { agent_id: id } = event.payload;
      const owner = membership[id];
      if (!owner) continue;
      const key = `${owner.kind}:${owner.team}`;
      const team = openTeam(teams, idle, key, event.seq);
      if (!identities.has(id)) {
        const member = event.type === "lead.status" ? 0 : ++team.members;
        identities.set(id, {
          team: key,
          hue: team.hue,
          order: team.order,
          member,
          letter:
            member === 0 || owner.kind === "agent" ? team.hue : `${team.hue}·${String(member)}`,
        });
      }
      if (event.type === "lead.status") {
        if (["starting", "working", "idle"].includes(event.payload.state)) team.active.add(id);
        else if (team.active.delete(id) && !team.active.size) idle.set(team.hue, event.seq);
        continue;
      }
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
