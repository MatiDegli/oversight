import { selectPendingChanges } from "../state";
import type { ChangeState, State } from "../state";

/** One change's file: what the left panel shows. */
export interface DiffTarget {
  changeId: string;
  path: string;
}

export interface FileTarget {
  path: string;
  agentId: string | null;
  startLine: number;
  endLine: number;
}

/**
 * What the person has opened. The graph's selected ring follows `selected`; the left panel shows the
 * live diff, review or file, with exactly one open at a time. Only the
 * person's actions change it, never an event.
 */
export interface View {
  selected: string | null;
  diff: DiffTarget | null;
  review: string | null;
  file: FileTarget | null;
}

export const initialView: View = { selected: null, diff: null, review: null, file: null };

export const panelOpen = (view: View) =>
  view.diff !== null || view.review !== null || view.file !== null;

/** Proposed and not yet decided: what review opens on. A revising change shows its live diff. */
export function isProposed(change: ChangeState): boolean {
  return (
    change.proposal !== null &&
    (change.status === "ready_for_review" || change.status === "conflict")
  );
}

/** The nodes a change adds, modifies or removes: what review keeps lit on the graph. */
export function changeNodeIds(change: ChangeState): string[] {
  const { graph } = change;
  return [
    ...graph.nodes_added.map((node) => node.id),
    ...graph.nodes_modified,
    ...graph.nodes_removed,
  ];
}

/**
 * What review keeps lit while the change waits for the person: its nodes and the proposal’s impact cone. Null once it is approved
 * or rejected, so the graph returns to its normal lights while the panel shows the outcome; an
 * approval that did not merge (status conflict) is still waiting and stays lit.
 */
export function litNodeIds(change: ChangeState | undefined): string[] | null {
  if (!change || change.status === "approved" || change.status === "rejected") return null;
  return [
    ...new Set([
      ...changeNodeIds(change),
      ...(change.proposal?.impact.entries.map((entry) => entry.node_id) ?? []),
    ]),
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
    if (!changeNodeIds(change).includes(nodeId)) continue;
    const node =
      state.graph?.nodes.find((candidate) => candidate.id === nodeId) ??
      change.graph.nodes_added.find((candidate) => candidate.id === nodeId);
    if (node && node.path in change.files) return { changeId: change.change_id, path: node.path };
  }
  return null;
}

/**
 * A click on a node selects it. When a pending change touches it, the panel shows that change:
 * review once it is proposed, its live diff before. Other existing nodes open their file when
 * file commands are available. An open review survives clicks on nodes with no pending diff.
 */
export function clickNode(state: State, view: View, nodeId: string, filesEnabled = false): View {
  const diff = diffForNode(state, nodeId);
  if (!diff) {
    if (view.review !== null) return { ...view, selected: nodeId };
    const node = state.graph?.nodes.find((candidate) => candidate.id === nodeId);
    if (!filesEnabled || !node) return { ...view, selected: nodeId };
    return {
      selected: nodeId,
      diff: null,
      review: null,
      file: {
        path: node.path,
        agentId: null,
        startLine: node.kind === "module" ? 1 : node.start_line,
        endLine: node.end_line,
      },
    };
  }
  const change = state.changes[diff.changeId];
  if (change && isProposed(change))
    return { selected: nodeId, diff: null, review: diff.changeId, file: null };
  return { selected: nodeId, diff, review: null, file: null };
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
    review: null,
    file: null,
  };
}

/**
 * Review of a proposed change, from the card's Review change or the queue counter, with its first
 * changed node selected so the camera frames it. Null for a change that is not proposed.
 */
export function openReview(state: State, view: View, changeId: string): View | null {
  const change = state.changes[changeId];
  if (!change || !isProposed(change)) return null;
  const path = Object.keys(change.files).sort()[0];
  const node =
    (path === undefined ? undefined : change.files[path]?.node_ids[0]) ?? changeNodeIds(change)[0];
  return { selected: node ?? view.selected, diff: null, review: changeId, file: null };
}

/** Cycle proposed changes in slot order; start with agent A when no review is open. */
export function nextReview(state: State, current: string | null = null): string | null {
  const proposed = pendingBySlot(state).filter(isProposed);
  if (proposed.length === 0) return null;
  const index = proposed.findIndex((change) => change.change_id === current);
  return proposed[(index + 1) % proposed.length]?.change_id ?? null;
}

export function closePanel(view: View): View {
  return { ...view, diff: null, review: null, file: null };
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
