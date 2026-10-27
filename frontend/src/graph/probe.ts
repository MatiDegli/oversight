import type { Core } from "cytoscape";

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
  zoom(): number;
  pan(): { x: number; y: number };
  /** True while the camera is easing to a place. */
  animating(): boolean;
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
      Object.fromEntries(cy.nodes(":childless").map((node) => [node.id(), node.position()])),
    zoom: () => cy.zoom(),
    pan: () => ({ ...cy.pan() }),
    animating: () => cy.animated(),
  };
  return () => {
    delete host.oversightGraph;
  };
}
