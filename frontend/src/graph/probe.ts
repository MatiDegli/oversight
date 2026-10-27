import type { Core } from "cytoscape";

import { visibleId } from "./foldVisibility";

export interface NodeBounds {
  id: string;
  distance?: number;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

/**
 * What the graph element answers to tests and to `make shots`. Cytoscape draws on a canvas, so a
 * node's place on screen is not in the DOM; this read-only view of it is the supported way to ask,
 * so no capture depends on Cytoscape's private fields. It exposes no instance and changes nothing.
 */
export interface GraphProbe {
  /** Centers of the nodes the selector matches, in pixels from the graph element's top left. */
  nodeCenters(selector?: string): { id: string; x: number; y: number }[];
  /** Model-space bounds, including labels and distance badges, for framing assertions. */
  nodeBounds(selector?: string): NodeBounds[];
  /** Layout positions of the nodes with no children, which no camera move may change. A box's own position follows its children. */
  positions(): Record<string, { x: number; y: number }>;
  representative(id: string): string;
  zoom(): number;
  pan(): { x: number; y: number };
  /** True while the camera is easing to a place. */
  animating(): boolean;
  /** Renderer measurements for screenshot checks, returned as a detached value. */
  metrics(): {
    viewport: { width: number; height: number };
    zoom: number;
    minLabelPx: number;
    edgeShape: string;
    visibleRows: number;
    visibleEdges: number;
    rows: number;
    edges: number;
  };
}

export type GraphProbeHost = HTMLElement & { oversightGraph?: GraphProbe };

export function exposeProbe(host: GraphProbeHost, cy: Core): () => void {
  host.oversightGraph = {
    nodeCenters: (selector = "node") =>
      cy.nodes(selector).map((node) => ({ id: node.id(), ...node.renderedPosition() })),
    nodeBounds: (selector = "node") =>
      cy.nodes(selector).map((node) => ({
        id: node.id(),
        distance: node.data("distance") as number | undefined,
        ...node.boundingBox({ includeLabels: true, includeOverlays: false }),
      })),
    positions: () =>
      Object.fromEntries(cy.nodes(":childless").map((node) => [node.id(), { ...node.position() }])),
    representative: (id) => visibleId(cy, id),
    zoom: () => cy.zoom(),
    pan: () => ({ ...cy.pan() }),
    animating: () => cy.animated() || cy.scratch("foldAnimating") === true,
    metrics: () => ({
      viewport: { width: cy.width(), height: cy.height() },
      zoom: cy.zoom(),
      minLabelPx: Math.min(...cy.nodes().map((node) => node.numericStyle("font-size") * cy.zoom())),
      // A call's shape: imports are not drawn.
      visibleRows: cy.nodes(":childless").filter((node) => {
        const b = node.renderedBoundingBox({ includeLabels: false, includeOverlays: false });
        return b.x1 >= 0 && b.y1 >= 0 && b.x2 <= cy.width() && b.y2 <= cy.height();
      }).length,
      visibleEdges: cy
        .edges()
        .not(".imports")
        .filter((edge) => {
          const b = edge.renderedBoundingBox({ includeLabels: false, includeOverlays: false });
          return b.x2 >= 0 && b.y2 >= 0 && b.x1 <= cy.width() && b.y1 <= cy.height();
        }).length,
      rows: cy.nodes(":childless").length,
      edges: cy.edges().not(".imports").length,
      edgeShape: String(cy.edges().not(".imports").first().style("curve-style")),
    }),
  };
  return () => {
    delete host.oversightGraph;
  };
}
