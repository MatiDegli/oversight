import type { State } from "./types";

export const selectAgents = (state: State) =>
  Object.values(state.agents).sort((a, b) => a.slot.localeCompare(b.slot));

export const selectChangeForAgent = (state: State, agentId: string) => {
  const agent = state.agents[agentId];
  return agent ? state.changes[agent.change_id] : undefined;
};

/** Each entry includes read/edit mode and ranges, without combining agents' focus. */
export const selectLitNodes = (state: State) => state.focus;

export const selectPendingChanges = (state: State) =>
  Object.values(state.changes).filter(
    (change) => change.status !== "approved" && change.status !== "rejected",
  );

export const selectHasReadyForReview = (state: State) =>
  Object.values(state.changes).some(
    (change) => change.status === "ready_for_review" || change.status === "conflict",
  );
