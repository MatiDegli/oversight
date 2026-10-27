// What the dot field asks of Cytoscape: where some nodes are drawn right now, and when that may have
// changed. It reads rendered boxes and nothing else, so the field never draws a node or an edge and
// the graph knows nothing about lights. GraphView attaches the instance it creates and detaches it
// when it destroys it.
import type { Core } from "cytoscape";

import type { Box } from "./model";
import { visibleId } from "../foldVisibility";

export interface BoxSource {
  attach(cy: Core): void;
  detach(): void;
  /** The rendered boxes of the nodes with these ids; an id with no node on the graph is left out. */
  boxes(ids: readonly string[]): Map<string, Box>;
  /** Calls `listener` after Cytoscape renders, resizes or is attached; returns its unsubscribe. */
  subscribe(listener: () => void): () => void;
}

export function createBoxSource(): BoxSource {
  const listeners = new Set<() => void>();
  let graph: Core | undefined;
  const notify = () => {
    for (const listener of [...listeners]) listener();
  };
  return {
    attach(cy) {
      graph?.off("render resize", notify);
      graph = cy;
      cy.on("render resize", notify);
      notify();
    },
    detach() {
      graph?.off("render resize", notify);
      graph = undefined;
      notify();
    },
    boxes(ids) {
      const found = new Map<string, Box>();
      if (!graph) return found;
      for (const id of ids) {
        const node = graph.getElementById(visibleId(graph, id));
        if (node.empty() || !node.isNode()) continue;
        const { x1, y1, x2, y2 } = node.renderedBoundingBox({
          includeLabels: false,
          includeOverlays: false,
          includeEdges: false,
        });
        found.set(id, { x1, y1, x2, y2 });
      }
      return found;
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
