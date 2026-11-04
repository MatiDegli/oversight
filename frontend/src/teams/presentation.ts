import type { AgentStatus, Plan } from "@contracts";
import type { State } from "../state/types";
import { selectTeams } from "../state/teamSelector";
import { agentLook, agentSlotStyle } from "../theme";
import type { AgentFamily, PaletteKey } from "../theme";

export type AgentPresentation = Readonly<{
  agentId: string;
  team: string;
  kind: "lead" | "plan" | "agent";
  letter: string;
  label: string;
  /** Compact badge text and visible plan caption when several plans reuse a subtask label. */
  planContext?: Readonly<{ shortLabel: string; caption: string }>;
  planId: string | null;
  subtask: number | null;
  /** Team-wide display position; subtask remains local to its plan. */
  ordinal?: number;
  hue: PaletteKey;
  family: AgentFamily;
  order: number;
}>;

/** Display order stays independent of runtime seats, including plans sharing a lead. */
export function compareAgentPresentation(
  a: AgentPresentation | undefined,
  b: AgentPresentation | undefined,
): number {
  if (!a || !b) return a ? -1 : b ? 1 : 0;
  return (
    a.order - b.order ||
    Number(a.kind !== "lead") - Number(b.kind !== "lead") ||
    (a.ordinal ?? a.subtask ?? 0) - (b.ordinal ?? b.subtask ?? 0) ||
    a.agentId.localeCompare(b.agentId)
  );
}

type TeamActivity = {
  hue: PaletteKey;
  order: number;
  active: Set<string>;
  pending: Set<string>;
  idleSince: number;
};
const palette: readonly PaletteKey[] = ["A", "B", "C", "D", "E", "F"];
const cache = new WeakMap<State, ReturnType<typeof buildPresentation>>();

/** Spreadsheet letters continue beyond Z; neither seats nor palette keys name a team. */
function teamLetter(index: number): string {
  let n = index + 1;
  let letter = "";
  while (n > 0) {
    n--;
    letter = String.fromCharCode(65 + (n % 26)) + letter;
    n = Math.floor(n / 26);
  }
  return letter;
}

/**
 * Q4 uses the accepted event prefix. An idle interval starts when the last acting member
 * stops (including a merge/rejection); pending changes are tracked separately and survive
 * pauses, stops, requests for changes and conflicts. An open lead reserves its hue even
 * while awaiting a message in idle; terminal lead statuses release it. Event timestamps determine elapsed idle time; equal times, invalid
 * legacy timestamps and teams still acting tie in selectTeams' first-observed order.
 * Reusing paint never removes or recolors the donor. No clock read or global allocator.
 */
function allocate(state: State, identities: ReturnType<typeof selectTeams>) {
  const teams = new Map<string, TeamActivity>();
  const changes = new Map<string, string>();
  const leadRevisions = new Map<string, number>();
  let eventTime = Date.parse(state.runStartedAt ?? "") || 0;
  function introduce(teamId: string) {
    if (teams.has(teamId)) return;
    const order = identities.teamOrder.indexOf(teamId);
    const candidates = [...teams.values()];
    const eligible = candidates.filter((team) => !team.pending.size);
    const donor = (eligible.length ? eligible : candidates).sort(
      (a, b) =>
        Number(Boolean(a.active.size)) - Number(Boolean(b.active.size)) ||
        (a.active.size || b.active.size ? 0 : a.idleSince - b.idleSince) ||
        a.order - b.order,
    )[0];
    const hue = palette[teams.size] ?? donor?.hue;
    if (!hue) throw new Error("Missing team palette donor");
    teams.set(teamId, { hue, order, active: new Set(), pending: new Set(), idleSince: eventTime });
  }
  for (const event of state.decisionEvents) {
    if (event.type === "lead.status") {
      const lead = event.payload;
      if (lead.revision <= (leadRevisions.get(lead.agent_id) ?? 0)) continue;
      leadRevisions.set(lead.agent_id, lead.revision);
    }
    const parsed = Date.parse(event.ts);
    if (Number.isFinite(parsed)) eventTime = Math.max(eventTime, parsed);
    const id =
      event.type === "plan.updated"
        ? event.payload.started_by
        : "agent_id" in event.payload
          ? event.payload.agent_id
          : null;
    const identity = id ? identities.byAgent[id] : undefined;
    if (!identity) continue;
    introduce(identity.team);
    const team = teams.get(identity.team);
    if (!team || !id) continue;
    const wasActive = team.active.size > 0;
    if (event.type === "agent.status") {
      const agent = event.payload;
      if (!changes.has(agent.change_id)) {
        changes.set(agent.change_id, identity.team);
        team.pending.add(agent.change_id);
      }
      if (["starting", "working", "revising"].includes(agent.state)) team.active.add(id);
      else team.active.delete(id);
    } else if (event.type === "lead.status") {
      if (["starting", "working", "idle"].includes(event.payload.state)) team.active.add(id);
      else team.active.delete(id);
    } else if (event.type === "change.proposed") {
      changes.set(event.payload.change_id, identity.team);
      team.pending.add(event.payload.change_id);
      team.active.delete(id);
    } else if (event.type === "change.resolved") {
      const resolution = event.payload;
      if (resolution.merge === "conflict" || resolution.decision === "changes_requested") {
        team.pending.add(resolution.change_id);
      } else {
        team.pending.delete(resolution.change_id);
        team.active.delete(id);
      }
    }
    if (wasActive && !team.active.size) team.idleSince = eventTime;
  }
  return teams;
}

function metadata(state: State) {
  const plans = new Map<string, Plan>();
  const origins = new Map<string, AgentStatus>();
  for (const event of state.decisionEvents) {
    if (
      event.type === "plan.updated" &&
      event.payload.revision > (plans.get(event.payload.plan_id)?.revision ?? 0)
    )
      plans.set(event.payload.plan_id, event.payload);
    if (event.type === "agent.status") {
      const agent = event.payload,
        first = origins.get(agent.agent_id);
      if (!first || (first.plan_id === null && agent.plan_id !== null))
        origins.set(agent.agent_id, agent);
    }
  }
  return { plans, origins };
}

/** Reserve waiting and ended siblings before assigning any display number. */
function teamOrdinals(state: State, plans: Map<string, Plan>) {
  const slots = new Map<string, number>();
  const assigned = new Map<string, number>();
  const locals = new Map<string, number>();
  const sizes = new Map<string, number>();
  const next = new Map<string, number>();
  const revisions = new Map<string, number>();
  for (const event of state.decisionEvents) {
    if (event.type !== "plan.updated") continue;
    const plan = event.payload;
    if (plan.revision <= (revisions.get(plan.plan_id) ?? 0)) continue;
    revisions.set(plan.plan_id, plan.revision);
    const team = plans.get(plan.plan_id)?.started_by ?? plan.plan_id;
    sizes.set(plan.plan_id, Math.max(sizes.get(plan.plan_id) ?? 0, plan.subtasks?.length ?? 0));
    plan.subtasks?.forEach((task, index) => {
      const key = `${plan.plan_id}:${String(index)}`;
      if (!slots.has(key)) {
        const ordinal = (next.get(team) ?? 0) + 1;
        next.set(team, ordinal);
        slots.set(key, ordinal);
      }
      const ordinal = slots.get(key);
      if (task.agent_id && ordinal !== undefined && !assigned.has(task.agent_id)) {
        assigned.set(task.agent_id, ordinal);
        locals.set(task.agent_id, index + 1);
      }
    });
  }
  return { assigned, locals, sizes };
}

function buildPresentation(state: State) {
  const identities = selectTeams(state);
  const teams = allocate(state, identities);
  const { plans, origins } = metadata(state);
  const ordinals = teamOrdinals(state, plans);
  const byAgent: Record<string, AgentPresentation> = {};
  for (const [agentId, identity] of Object.entries(identities.byAgent)) {
    const order = identities.teamOrder.indexOf(identity.team);
    const letter = teamLetter(order);
    const origin = origins.get(agentId);
    const planId = origin?.plan_id ?? null;
    const plan = planId ? plans.get(planId) : undefined;
    const index = plan?.subtasks?.findIndex((task) => task.agent_id === agentId) ?? -1;
    // A waiting or finished sibling counts. Never derive the index from running agents.
    const lone = identity.kind !== "lead" && (ordinals.sizes.get(planId ?? "") ?? 0) <= 1;
    const local = ordinals.locals.get(agentId) ?? (index >= 0 ? index + 1 : null);
    const subtask = lone ? null : local;
    const hue = teams.get(identity.team)?.hue;
    if (!hue) continue;
    byAgent[agentId] = Object.freeze({
      agentId,
      ...identity,
      letter,
      label:
        subtask === null
          ? letter
          : `${letter}·${String(ordinals.assigned.get(agentId) ?? subtask)}`,
      planId,
      subtask,
      ordinal: ordinals.assigned.get(agentId) ?? subtask ?? 0,
      hue,
      family: agentLook[hue].family,
      order,
    });
  }
  return Object.freeze({
    byAgent: Object.freeze(byAgent),
    teamOrder: identities.teamOrder,
  });
}

/** One immutable lookup for every surface. Unknown/usage-only actors have no coding identity. */
export function selectPresentation(state: State) {
  const existing = cache.get(state);
  if (existing) return existing;
  const presentation = buildPresentation(state);
  cache.set(state, presentation);
  return presentation;
}

export function presentationFor(state: State, agentId: string | null | undefined) {
  return agentId ? selectPresentation(state).byAgent[agentId] : undefined;
}

export const presentationStyle = (identity: AgentPresentation | undefined) =>
  agentSlotStyle(identity?.hue);
