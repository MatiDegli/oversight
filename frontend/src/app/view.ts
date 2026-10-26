import { selectPendingChanges } from "../state";
import type { ChangeState, State } from "../state";

/** One change's file: what the left panel shows. */
export interface DiffTarget {
  changeId: string;
  path: string;
}

/**
 * What the person has opened. The graph's selected ring follows `selected`; the left panel is open
 * exactly while `diff` is set. Only the person's actions change it, never an event.
 */
export interface View {
  selected: string | null;
  diff: DiffTarget | null;
}

export const initialView: View = { selected: null, diff: null };

function touchedNodes(change: ChangeState): string[] {
  const { graph } = change;
  return [
    ...graph.nodes_added.map((node) => node.id),
    ...graph.nodes_modified,
    ...graph.nodes_removed,
  ];
}

/** Unresolved changes, agent A's first, so a node two agents touch opens the same diff each time. */
function pendingBySlot(state: State): ChangeState[] {
  const slot = (change: ChangeState) => state.agents[change.agent_id]?.slot ?? "~";
  return selectPendingChanges(state).sort(
    (a, b) => slot(a).localeCompare(slot(b)) || a.change_id.localeCompare(b.change_id),
  );
}

/**
 * The unresolved change and file that touch a node, or null. A file's edits name their nodes; a
 * node the change's graph names (such as one it removed) maps to its own file in the change.
 */
export function diffForNode(state: State, nodeId: string): DiffTarget | null {
  const changes = pendingBySlot(state);
  for (const change of changes) {
    for (const [path, entry] of Object.entries(change.files)) {
      if (entry.node_ids.includes(nodeId)) return { changeId: change.change_id, path };
    }
  }
  for (const change of changes) {
    if (!touchedNodes(change).includes(nodeId)) continue;
    const node =
      state.graph?.nodes.find((candidate) => candidate.id === nodeId) ??
      change.graph.nodes_added.find((candidate) => candidate.id === nodeId);
    if (node && node.path in change.files) return { changeId: change.change_id, path: node.path };
  }
  return null;
}

/** A click on a node selects it; when a pending change touches it, the panel shows that diff. */
export function clickNode(state: State, view: View, nodeId: string): View {
  const diff = diffForNode(state, nodeId);
  return { selected: nodeId, diff: diff ?? view.diff };
}

/**
 * The agent card's Live diff: the change's first file, with the node its edit named selected. Null
 * when the change has no file yet or is resolved, so the card shows no button that does nothing.
 */
export function openChange(state: State, view: View, changeId: string): View | null {
  const change = pendingBySlot(state).find((candidate) => candidate.change_id === changeId);
  const path = change ? Object.keys(change.files).sort()[0] : undefined;
  if (!change || path === undefined) return null;
  return {
    selected: change.files[path]?.node_ids[0] ?? view.selected,
    diff: { changeId, path },
  };
}

export function closePanel(view: View): View {
  return { ...view, diff: null };
}

/** Escape closes the panel unless something inside it, such as Monaco's find box, took the key. */
export function closesPanel(
  event: Pick<KeyboardEvent, "key" | "defaultPrevented" | "isComposing">,
) {
  return event.key === "Escape" && !event.defaultPrevented && !event.isComposing;
}

/** Changes waiting on the person, across agents: the top bar's neutral counter. */
export function readyCount(state: State): number {
  return selectPendingChanges(state).filter(
    (change) => change.status === "ready_for_review" || change.status === "conflict",
  ).length;
}
