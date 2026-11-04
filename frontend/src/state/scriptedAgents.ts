import type { LeadStatus } from "@contracts";
import type { State } from "./types";

/** Provenance comes only from the current run's offered event profiles. */
export function isScriptedAgent(state: State, agentId: string): boolean {
  if (!state.run) return false;
  let lead: LeadStatus | undefined;
  for (const event of state.decisionEvents) {
    if (
      event.type === "lead.status" &&
      event.payload.agent_id === agentId &&
      event.payload.revision > (lead?.revision ?? 0)
    )
      lead = event.payload;
  }
  const modelId = lead?.model ?? state.agents[agentId]?.model;
  return state.run.models.some((profile) => profile.id === modelId && profile.provider === "fake");
}
