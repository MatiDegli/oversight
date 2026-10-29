import type { ServerEvent } from "@contracts";
import type { State } from "./types";

function patchItems<T extends { id: string }>(
  current: T[],
  added: T[],
  changed: T[],
  removed: string[],
): T[] {
  const removedIds = new Set(removed);
  const items = Object.fromEntries(
    current.filter((item) => !removedIds.has(item.id)).map((item) => [item.id, item]),
  );
  for (const item of [...added, ...changed]) items[item.id] = item;
  return Object.values(items);
}

type Event = Extract<ServerEvent, { type: "graph.snapshot" | "graph.delta" }>;

export function applyGraphEvent(state: State, next: State, event: Event): State {
  switch (event.type) {
    case "graph.snapshot":
      return { ...next, graph: event.payload };
    case "graph.delta": {
      const delta = event.payload;
      if (!state.graph || state.graph.version !== delta.from_version) {
        return { ...state, gap: true };
      }
      const nodes = patchItems(
        state.graph.nodes,
        delta.nodes_added,
        delta.nodes_changed,
        delta.nodes_removed,
      );
      const nodeIds = new Set(nodes.map((node) => node.id));
      return {
        ...next,
        graph: {
          version: delta.to_version,
          commit: delta.commit,
          nodes,
          edges: patchItems(
            state.graph.edges,
            delta.edges_added,
            delta.edges_changed,
            delta.edges_removed,
          ).filter((edge) => nodeIds.has(edge.src) && nodeIds.has(edge.dst)),
          parse_errors: delta.parse_errors,
          positions: state.graph.positions,
        },
      };
    }
  }
}
