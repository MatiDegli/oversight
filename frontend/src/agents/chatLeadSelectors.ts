import type { LeadStatus, Plan } from "@contracts";
import type { State } from "../state";
import { selectChatAgents, teamIdentities } from "./chatAgentSelectors";

export const leadLabels: Record<LeadStatus["state"], string> = {
  starting: "Starting",
  working: "Working",
  idle: "Idle",
  stopped: "Stopped",
  halted: "Halted",
  done: "Done",
};

/** Retained revisions are the only source for goals, status, details and limits. */
export function selectLeadStatuses(state: State) {
  const leads = new Map<string, LeadStatus>();
  for (const event of state.decisionEvents) {
    if (event.type !== "lead.status") continue;
    const lead = event.payload;
    if (lead.revision > (leads.get(lead.agent_id)?.revision ?? 0)) leads.set(lead.agent_id, lead);
  }
  return leads;
}

export function selectLeadLimits(state: State) {
  let limits: LeadStatus["limits"] = null;
  for (const event of state.decisionEvents) {
    if (event.type === "lead.status") limits = event.payload.limits ?? limits ?? null;
  }
  return limits;
}

function teamLine(members: number, working: number, needsYou: number, plans: number) {
  if (!members)
    return plans ? `${String(plans)} ${plans === 1 ? "plan needs" : "plans need"} you` : null;
  const parts = [
    working ? `${String(working)} working` : null,
    needsYou ? `${String(needsYou)} ${needsYou === 1 ? "needs" : "need"} you` : null,
  ].filter(Boolean);
  return `${String(members)} ${members === 1 ? "agent" : "agents"}${parts.length ? ` · ${parts.join(", ")}` : ""}`;
}

export function selectChatLeads(state: State) {
  const identities = teamIdentities(state);
  const plans = new Map<string, Plan>();
  for (const event of state.decisionEvents) {
    if (
      event.type === "plan.updated" &&
      event.payload.revision > (plans.get(event.payload.plan_id)?.revision ?? 0)
    )
      plans.set(event.payload.plan_id, event.payload);
  }
  return [...selectLeadStatuses(state)].flatMap(([id, status]) => {
    const identity = identities.get(id);
    if (!identity) return [];
    const members = Object.values(state.agents).filter(
      (agent) => identities.get(agent.agent_id)?.team === identity.team,
    );
    const working = members.filter(
      (agent) => agent.state === "working" || agent.state === "revising",
    ).length;
    const proposed = [...plans.values()].filter(
      (plan) => plan.started_by === id && plan.state === "proposed",
    ).length;
    const needsYou =
      members.filter((agent) => agent.state === "ready_for_review").length + proposed;
    // A null detail does not erase the latest detail within the current status.
    let detail: string | null = null;
    let previous: LeadStatus["state"] | undefined;
    let revision = 0;
    for (const event of state.decisionEvents) {
      if (
        event.type !== "lead.status" ||
        event.payload.agent_id !== id ||
        event.payload.revision <= revision
      )
        continue;
      revision = event.payload.revision;
      if (previous !== event.payload.state) detail = null;
      previous = event.payload.state;
      detail = event.payload.detail ?? detail;
    }
    return [
      {
        ...identity,
        id,
        status,
        label: leadLabels[status.state],
        done: ["done", "stopped", "halted"].includes(status.state),
        teamLine: teamLine(members.length, working, needsYou, proposed),
        activity: [detail, `Step ${String(status.step)}`].filter(Boolean).join(" · "),
      },
    ];
  });
}
export type ChatLead = ReturnType<typeof selectChatLeads>[number];

export function selectChatRows(state: State) {
  return [
    ...selectChatLeads(state).map((row) => ({ kind: "lead" as const, ...row })),
    ...selectChatAgents(state).map((row) => ({ kind: "agent" as const, ...row })),
  ].sort((a, b) => a.order - b.order || a.member - b.member);
}
