import type { GraphNode } from "@contracts";

import { selectPendingChanges } from "../state";
import type { State } from "../state";

export interface FoldState {
  expanded: ReadonlySet<string>;
  seen: ReadonlySet<string>;
}
export const initialFolds = (): FoldState => ({ expanded: new Set(), seen: new Set() });
export type FoldAction =
  | { type: "toggle"; id: string }
  | {
      type: "change";
      revision: string;
      classes: readonly string[];
    };

/** Clicks always toggle. Only a fresh edit revision can reopen a class the person folded again. */
export function reduceFolds(state: FoldState, action: FoldAction): FoldState {
  if (action.type === "change" && state.seen.has(action.revision)) return state;
  const expanded = new Set(state.expanded);
  const seen = new Set(state.seen);
  if (action.type === "toggle") {
    if (expanded.has(action.id)) expanded.delete(action.id);
    else expanded.add(action.id);
  } else {
    seen.add(action.revision);
    for (const id of action.classes) expanded.add(id);
  }
  return { expanded, seen };
}

/** Observe actual changed contents, never read focus, step events, or the review mode. */
export function observeEdits(folds: FoldState, state: State): FoldState {
  const nodes = new Map(state.graph?.nodes.map((node) => [node.id, node]));
  const pending = selectPendingChanges(state);
  for (const change of pending)
    for (const node of change.graph.nodes_added) nodes.set(node.id, node);
  const classesOf = (id: string) => {
    const classes: string[] = [];
    let node: GraphNode | undefined = nodes.get(id);
    while (node?.parent_id) {
      node = nodes.get(node.parent_id);
      if (node?.kind === "class") classes.push(node.id);
    }
    return classes;
  };
  for (const change of pending) {
    const ids = [
      ...change.graph.nodes_modified,
      ...change.graph.nodes_added.map((node) => node.id),
    ];
    const classes = [...new Set(ids.flatMap(classesOf))];
    if (!classes.length) continue;
    const revision = JSON.stringify([
      change.change_id,
      ids,
      Object.entries(change.files)
        .sort(([a], [b]) => (a < b ? -1 : 1))
        .map(([path, entry]) => [path, entry.file.modified]),
    ]);
    folds = reduceFolds(folds, { type: "change", revision, classes });
  }
  return folds;
}
