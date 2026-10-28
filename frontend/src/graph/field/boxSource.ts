// What the dot field asks of Cytoscape: where some nodes are drawn right now, and when that may have
// changed. It reads rendered boxes and nothing else, so the field never draws a node or an edge and
// the graph knows nothing about lights. GraphView attaches the instance it creates and detaches it
// when it destroys it.
import type { Core } from "cytoscape";

import { getActiveTheme } from "../../theme";
import type { XY } from "../orthogonal";

import type { Box } from "./model";
import { visibleId } from "../foldVisibility";
import { observeFirstGraphDraw } from "../firstDraw";

export interface BoxSource {
  attach(cy: Core): void;
  zoom?(): number;
  /** The actual rendered edge in travel order, or null when there is no edge. */
  route?(from: string, to: string): XY[] | null;
  detach(): void;
  /** The rendered boxes of the nodes with these ids; an id with no node on the graph is left out. */
  boxes(ids: readonly string[]): Map<string, Box>;
  /** Calls `listener` after Cytoscape renders, resizes or is attached; returns its unsubscribe. */
  subscribe(listener: () => void): () => void;
}

export function createBoxSource(): BoxSource {
  const listeners = new Set<() => void>();
  let graph: Core | undefined;
  let stopFirstDraw: () => void = () => undefined;
  const notify = () => {
    for (const listener of [...listeners]) listener();
  };
  return {
    attach(cy) {
      stopFirstDraw();
      stopFirstDraw = observeFirstGraphDraw(cy);
      graph?.off("render resize", notify);
      graph = cy;
      cy.on("render resize", notify);
      notify();
    },
    detach() {
      stopFirstDraw();
      graph?.off("render resize", notify);
      graph = undefined;
      notify();
    },
    zoom: () => graph?.zoom() ?? 1,
    route(from, to) {
      if (!graph) return null;
      const a = visibleId(graph, from);
      const b = visibleId(graph, to);
      const edge = graph
        .edges()
        .filter(
          (item) =>
            (item.source().id() === a && item.target().id() === b) ||
            (item.source().id() === b && item.target().id() === a),
        )
        .first();
      if (edge.empty() || !edge.isEdge() || edge.hasClass("unroutable")) return null;
      const start = edge.renderedSourceEndpoint();
      const end = edge.renderedTargetEndpoint();
      if (!validPoint(start) || !validPoint(end)) return null;
      const bends = edge.renderedSegmentPoints();
      const points = [start, ...(Array.isArray(bends) ? bends : []), end];
      return edge.source().id() === a ? points : points.reverse();
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
        // An open container emits from its header row, never its entire frame.
        const header = Number.parseFloat(getActiveTheme()["frame-header-height"]) * graph.zoom();
        found.set(id, { x1, y1, x2, y2: node.isParent() ? Math.min(y2, y1 + header) : y2 });
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

function validPoint(value: unknown): value is XY {
  return (
    typeof value === "object" &&
    value !== null &&
    "x" in value &&
    "y" in value &&
    typeof value.x === "number" &&
    typeof value.y === "number" &&
    Number.isFinite(value.x) &&
    Number.isFinite(value.y)
  );
}
