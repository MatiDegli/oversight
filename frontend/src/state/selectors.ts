import type { AgentFocus, Slot } from "@contracts";

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

export interface NodeMarks {
  pending: boolean;
  pendingSlots: Slot[];
  focus: Partial<Record<Slot, AgentFocus["mode"]>>;
}

/** Facts only: unresolved changes survive missing agents; focus follows runtime status. */
export function selectNodeMarks(state: State): Record<string, NodeMarks> {
  const marks: Record<string, NodeMarks> = {};
  const get = (id: string) => (marks[id] ??= { pending: false, pendingSlots: [], focus: {} });
  for (const change of selectPendingChanges(state)) {
    const slot = state.agents[change.agent_id]?.slot;
    for (const id of [
      ...change.graph.nodes_added.map((node) => node.id),
      ...change.graph.nodes_modified,
      ...change.graph.nodes_removed,
    ]) {
      const mark = get(id);
      mark.pending = true;
      if (slot && !mark.pendingSlots.includes(slot)) mark.pendingSlots.push(slot);
      mark.pendingSlots.sort();
    }
  }
  for (const focus of Object.values(state.focus)) {
    const agent = state.agents[focus.agent_id];
    if (!agent || (agent.state !== "working" && agent.state !== "revising")) continue;
    for (const id of focus.node_ids) get(id).focus[agent.slot] = focus.mode;
  }
  return marks;
}
