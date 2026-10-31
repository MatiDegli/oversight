import type { Slot } from "@contracts";
import { selectPendingChanges } from "./selectors";
import type { ChangeState, State } from "./types";

export interface OverlapEntry {
  slot: Slot;
  agentId: string;
  nodes: { id: string; name: string }[];
}
export interface Overlap {
  sharedEdits: OverlapEntry[];
  readOfOthers: OverlapEntry[];
}

const changing = (change: ChangeState) => [
  ...change.graph.nodes_added.map((node) => node.id),
  ...change.graph.nodes_modified,
  ...change.graph.nodes_removed,
];

/** Observed reads and pending edits only; neither focus nor impact cones imply overlap. */
export function selectOverlap(state: State, agentId: string): Overlap {
  const overlap: Overlap = { sharedEdits: [], readOfOthers: [] };
  const current = state.agents[agentId]?.change_id;
  const pending = selectPendingChanges(state);
  const own = pending.find((change) => change.change_id === current);
  const edits = new Set(own ? changing(own) : []);
  const reads = new Set(current ? state.changeReads[current] : []);
  const names = new Map(
    Object.values(state.changes).flatMap((change) =>
      change.graph.nodes_added.map((node) => [node.id, node.qualname] as const),
    ),
  );
  for (const node of state.graph?.nodes ?? []) names.set(node.id, node.qualname);
  const others = new Map<string, Set<string>>();
  for (const change of pending) {
    if (change.agent_id === agentId) continue;
    const ids = others.get(change.agent_id) ?? new Set<string>();
    changing(change).forEach((id) => ids.add(id));
    others.set(change.agent_id, ids);
  }
  const agents = Object.values(state.agents).sort(
    (a, b) => a.slot.localeCompare(b.slot) || a.agent_id.localeCompare(b.agent_id),
  );
  for (const agent of agents) {
    const ids = [...(others.get(agent.agent_id) ?? [])].sort();
    for (const kind of ["sharedEdits", "readOfOthers"] as const) {
      const nodes = ids
        .filter((id) => (kind === "sharedEdits" ? edits.has(id) : reads.has(id) && !edits.has(id)))
        .map((id) => ({ id, name: names.get(id) ?? id }));
      if (nodes.length) overlap[kind].push({ slot: agent.slot, agentId: agent.agent_id, nodes });
    }
  }
  return overlap;
}
