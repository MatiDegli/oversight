import type { Route } from "./routes";
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
  /**
   * Model-space bounds for framing assertions: with labels and distance badges unless `labels` is
   * false, which gives the drawn box alone.
   */
  nodeBounds(selector?: string, labels?: boolean): NodeBounds[];
  /** Layout positions of the nodes with no children, which no camera move may change. A box's own position follows its children. */
  positions(): Record<string, { x: number; y: number }>;
  representative(id: string): string;
  /** Drawn node box in host pixels, excluding labels and overlays; folded nodes return null. */
  level(): "cards" | "map";
  renderedBox(
    id: string,
  ): { x1: number; y1: number; x2: number; y2: number; w: number; h: number } | null;
  /** Computed renderer styles, for checking pending borders and agent colors on the canvas. */
  appearance(selector: string): {
    id: string;
    borderStyle: string;
    borderColor: string;
    lineStyle: string;
    opacity: number;
  }[];
  routes(): {
    id: string;
    source: string;
    target: string;
    count: number;
    possible: boolean;
    visible: boolean;
    points: { x: number; y: number }[];
  }[];
  cost(): { dragMs: number[] };
  draggable(): string[];
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
    nodeBounds: (selector = "node", labels = true) =>
      cy.nodes(selector).map((node) => ({
        id: node.id(),
        distance: node.data("distance") as number | undefined,
        ...node.boundingBox({ includeLabels: labels, includeOverlays: false }),
      })),
    positions: () =>
      Object.fromEntries(cy.nodes(":childless").map((node) => [node.id(), { ...node.position() }])),
    representative: (id) => visibleId(cy, id),
    level: () => (host.dataset.mapLevel === "map" ? "map" : "cards"),
    renderedBox: (id) => {
      const node = cy.getElementById(id);
      if (node.empty() || !node.isNode() || visibleId(cy, id) !== id || !node.visible())
        return null;
      return {
        ...node.renderedBoundingBox({
          includeLabels: false,
          includeOverlays: false,
          includeUnderlays: false,
        }),
      };
    },
    appearance: (selector) =>
      cy.elements(selector).map((element) => ({
        id: element.id(),
        borderStyle: String(element.style("border-style")),
        borderColor: String(element.style("border-color")),
        lineStyle: String(element.style("line-style")),
        opacity: Number(element.numericStyle("opacity")),
      })),
    draggable: () =>
      cy
        .nodes()
        .filter((n) => n.grabbable())
        .map((n) => n.id()),
    cost: () => ({
      dragMs: [...((cy.scratch("dragCosts") as number[] | undefined) ?? [])],
    }),
    routes: () =>
      cy.edges(".calls").map((edge) => {
        const route = edge.data("route") as Route | undefined;
        const from = edge.source().position();
        const to = edge.target().position();
        const start = { x: from.x + (route?.s[0] ?? 0), y: from.y + (route?.s[1] ?? 0) };
        const end = { x: to.x + (route?.t[0] ?? 0), y: to.y + (route?.t[1] ?? 0) };
        const vx = end.x - start.x,
          vy = end.y - start.y;
        const length = Math.hypot(vx, vy);
        return {
          id: edge.id(),
          source: edge.source().id(),
          target: edge.target().id(),
          count: (edge.data("callCount") as number | undefined) ?? 1,
          possible: edge.hasClass("possible"),
          visible: edge.style("display") !== "none",
          points: route
            ? [
                start,
                ...route.w.map((w, i) => ({
                  x: start.x + w * vx - ((route.d[i] ?? 0) * vy) / length,
                  y: start.y + w * vy + ((route.d[i] ?? 0) * vx) / length,
                })),
                end,
              ]
            : edge.hasClass("possible")
              ? [edge.sourceEndpoint(), edge.targetEndpoint()].filter(Boolean)
              : [],
        };
      }),
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
          if (edge.style("display") === "none") return false;
          const b = edge.renderedBoundingBox({ includeLabels: false, includeOverlays: false });
          return b.x2 >= 0 && b.y2 >= 0 && b.x1 <= cy.width() && b.y1 <= cy.height();
        }).length,
      rows: cy.nodes(":childless").length,
      edges: cy.edges().not(".imports").length,
      edgeShape: String(cy.edges().not(".imports").first().style("curve-style")),
    }),
  };
  const notify = () => {
    if (typeof host.dispatchEvent === "function")
      host.dispatchEvent(new CustomEvent("graphrender"));
  };
  cy.on("render resize", notify);
  notify();
  return () => {
    cy.off("render resize", notify);
    delete host.oversightGraph;
  };
}
