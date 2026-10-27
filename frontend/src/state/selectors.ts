import type { AgentFocus, Slot } from "@contracts";

import type { State } from "./types";

/**
 * Derived selectors below allocate on every call (including selectAgents and
 * selectPendingChanges). Zustand callers select the source state/slices first,
 * then memoize these projections with useMemo; do not use them as getSnapshot.
 * Card data only: selection and navigation belong to the app.
 */
export function selectAgentCard(state: State, agentId: string) {
  const agent = state.agents[agentId];
  if (!agent) return undefined;
  const steps = state.steps[agentId] ?? [];
  return {
    agent,
    /** The model's own name from the run ("Muse Spark"); the id when the run does not list it. */
    modelLabel: state.run?.models.find((model) => model.id === agent.model)?.label ?? agent.model,
    steps: [...steps].reverse(),
    step: Math.max(agent.step, ...steps.map((step) => step.step)),
    readyForReview: agent.state === "ready_for_review",
    /** What the agent proposed, with its tests; null until change.proposed arrives. */
    proposal: state.changes[agent.change_id]?.proposal ?? null,
    /** Null: unknown, as the server reported it. Undefined: no usage tick yet, so nothing to say. */
    cost: state.agentCosts[agentId],
  };
}

export type AgentCardData = NonNullable<ReturnType<typeof selectAgentCard>>;

/** Select by change as well as path: parallel agents may edit the same file. */
export function selectLiveDiff(state: State, changeId: string, path: string) {
  const change = state.changes[changeId];
  const entry = change?.files[path];
  if (!change || !entry) return undefined;
  return {
    changeId,
    agentId: change.agent_id,
    path: entry.file.path,
    original: entry.file.original ?? "",
    modified: entry.file.modified ?? "",
    status: entry.file.status,
    author: entry.author,
    linesAdded: entry.file.lines_added,
    linesRemoved: entry.file.lines_removed,
  };
}

export type LiveDiffData = NonNullable<ReturnType<typeof selectLiveDiff>>;

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

/** What the graph's heading says: the repository, what is drawn of it, and who is at work. */
export function selectGraphSummary(state: State) {
  const nodes = state.graph?.nodes.filter((node) => !node.is_test) ?? [];
  return {
    repo: state.run?.workspace ?? null,
    modules: nodes.filter((node) => node.kind === "module").length,
    // Methods count as functions: the graph draws both the same way.
    functions: nodes.filter((node) => node.kind === "function" || node.kind === "method").length,
    working: Object.values(state.agents).filter(
      (agent) =>
        agent.state === "starting" || agent.state === "working" || agent.state === "revising",
    ).length,
  };
}
