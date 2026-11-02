import type { State } from "./types";

type Team = Readonly<{ team: string; kind: "lead" | "plan" | "agent" }>;
type Teams = Readonly<{
  byAgent: Readonly<Record<string, Team>>;
  teamOrder: readonly string[];
}>;

const cache = new WeakMap<State, Teams>();

/** Run-local identities and first-start order, resolved over the complete accepted prefix. */
export function selectTeams(state: State): Teams {
  const cached = cache.get(state);
  if (cached) return cached;

  const observed = new Set<string>();
  const agentPlan = new Map<string, string>();
  const planLead = new Map<string, string | null>();
  const leads = new Set<string>();
  // Coding ticks omit plan_id; plan-tagged ticks belong to usage-only planners (_tick.py,
  // runtime_plans.py). Retained decision envelopes already follow accepted sequence order.
  for (const event of state.decisionEvents) {
    switch (event.type) {
      case "agent.status": {
        const { agent_id, plan_id } = event.payload;
        observed.add(agent_id);
        if (plan_id !== null && !agentPlan.has(agent_id)) agentPlan.set(agent_id, plan_id);
        break;
      }
      case "plan.updated": {
        const { plan_id, started_by } = event.payload;
        planLead.set(plan_id, started_by);
        if (started_by !== null) {
          observed.add(started_by);
          leads.add(started_by);
        }
        break;
      }
      case "lead.status":
        observed.add(event.payload.agent_id);
        leads.add(event.payload.agent_id);
        break;
      case "change.assessed":
      case "change.facts_recorded":
      case "change.proposed":
      case "change.resolved":
      case "error":
      case "queue.updated":
      case "revert.resolved":
        break;
    }
  }

  const entries = [...observed].map((id): [string, Team] => {
    const plan = agentPlan.get(id);
    const lead = plan === undefined ? null : planLead.get(plan);
    const identity: Team = leads.has(id)
      ? { team: id, kind: "lead" }
      : lead != null
        ? { team: lead, kind: "lead" }
        : plan !== undefined
          ? { team: plan, kind: "plan" }
          : { team: id, kind: "agent" };
    return [id, Object.freeze(identity)];
  });
  // Resolve before ordering: the earliest member can introduce a lead discovered later.
  const teamOrder = [...new Set(entries.map(([, identity]) => identity.team))];
  const result = Object.freeze({
    byAgent: Object.freeze(Object.fromEntries(entries)),
    teamOrder: Object.freeze(teamOrder),
  });
  cache.set(state, result);
  return result;
}
