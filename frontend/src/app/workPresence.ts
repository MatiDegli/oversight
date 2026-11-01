import type { FocusRange, Slot } from "@contracts";
import type { State } from "../state";

export interface WorkFile {
  path: string;
  ranges: FocusRange[];
  functions: string[];
  lines_added?: number;
  lines_removed?: number;
}
export interface WorkGroup {
  agentId: string;
  slot: Slot;
  mode: "read" | "edit" | "pending";
  changeId?: string;
  conflict?: "merge" | "tests";
  files: WorkFile[];
}

function currentFiles(state: State, agentId: string): WorkFile[] {
  const focus = state.focus[agentId];
  if (!focus) return [];
  const own = state.changes[state.agents[agentId]?.change_id ?? ""];
  const nodes = new Map(
    [...(state.graph?.nodes ?? []), ...(own?.graph.nodes_added ?? [])].map((n) => [n.id, n]),
  );
  const files = new Map<string, WorkFile>();
  const file = (path: string) => {
    let entry = files.get(path);
    if (!entry) {
      entry = { path, ranges: [], functions: [] };
      files.set(path, entry);
    }
    return entry;
  };
  for (const range of focus.ranges) {
    const entry = file(range.path);
    if (
      !entry.ranges.some((r) => r.start_line === range.start_line && r.end_line === range.end_line)
    )
      entry.ranges.push({ ...range });
  }
  for (const id of focus.node_ids) {
    const node = nodes.get(id);
    if (!node) continue;
    const entry = file(node.path);
    const name = node.qualname || node.name;
    if ((node.kind === "function" || node.kind === "method") && !entry.functions.includes(name))
      entry.functions.push(name);
  }
  return [...files.values()];
}

/** One event-only projection shared by Chat and Files; no history, effects or graph renderer. */
export function selectWorkPresence(state: State) {
  const current: WorkGroup[] = [],
    pending: WorkGroup[] = [];
  const agents = Object.values(state.agents).sort(
    (a, b) => a.slot.localeCompare(b.slot) || a.agent_id.localeCompare(b.agent_id),
  );
  for (const agent of agents) {
    const identity = { agentId: agent.agent_id, slot: agent.slot };
    if (agent.state === "working" || agent.state === "revising") {
      const files = currentFiles(state, agent.agent_id);
      const focus = state.focus[agent.agent_id];
      if (files.length && focus) current.push({ ...identity, mode: focus.mode, files });
    }
    for (const change of Object.values(state.changes)) {
      if (change.agent_id !== agent.agent_id || !change.proposal) continue;
      if (change.status !== "ready_for_review" && change.status !== "conflict") continue;
      if (!change.proposal.files.length) continue;
      pending.push({
        ...identity,
        mode: "pending",
        changeId: change.change_id,
        conflict: change.status === "conflict" ? change.resolution?.conflict?.kind : undefined,
        files: change.proposal.files.map((f) => ({ ...f, ranges: [], functions: [] })),
      });
    }
  }
  return { hasAgents: agents.length > 0, current, pending };
}
