// Orthogonal ("taxi") edge routes through the gutters between module frames, as in the Signal
// sketch: a call leaves its caller's row at the right, runs along a lane in the gutter and enters
// its callee's row at the left, with the arrowhead on the border. Back and same-column calls
// detour outside frames. Callers of the same target share its incoming trunk.
//
// The router is a pure function of boxes and edges. It places no node: it only says where each edge
// meets its two nodes (offsets from the node's centre, in px) and where it bends, in the terms
// Cytoscape's `segments` style takes (weights along the line between the two ends, distances
// across it), which the stylesheet reads from the edge's data.
import type { Core, EdgeCollection } from "cytoscape";

import { obstacleChecks, orthogonalPath, simplify } from "./orthogonal";
import type { XY } from "./orthogonal";

/** A node's box, in graph units, and the line an edge meets it on. */
export interface RouteBox {
  id: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  /** The row's middle, or the middle of a frame's header band: where edges meet it. */
  anchorY: number;
  /** The outermost frame that holds this box (its own id when it is not inside one). */
  top: string;
}

export interface RouteEdge {
  id: string;
  source: string;
  target: string;
}

/** What an edge's data carries for the stylesheet. */
export interface Route {
  /** The edge's two ends as offsets from their node's centre. */
  s: [number, number];
  t: [number, number];
  /** The bends along the line between the ends, as weights and as distances across it. */
  w: number[];
  d: number[];
}

export interface Tuning {
  /** Distance between two lanes in a gutter, and between two lanes at a frame's left. */
  laneGap: number;
  /** Distance between two edges on one side of a node. */
  portGap: number;
  /** How far inside a node's border its first and last edge stay. */
  portMargin: number;
  /** A gutter narrower than this is not one: it is a frame's own padding. */
  minGutter: number;
  /** The first loop's distance from the left borders it connects. */
  loopInset: number;
}

const center = (box: RouteBox) => ({ x: (box.x1 + box.x2) / 2, y: (box.y1 + box.y2) / 2 });

/** Encode explicit bends in Cytoscape's endpoint-relative coordinates. */
function encode(points: XY[], from: RouteBox, to: RouteBox): Route {
  const start = points[0] ?? center(from);
  const end = points.at(-1) ?? center(to);
  const vx = end.x - start.x;
  const vy = end.y - start.y;
  const length = Math.hypot(vx, vy);
  const bends = points.slice(1, -1);
  const a = center(from);
  const b = center(to);
  return {
    s: [start.x - a.x, start.y - a.y],
    t: [end.x - b.x, end.y - b.y],
    w: bends.map((p) => ((p.x - start.x) * vx + (p.y - start.y) * vy) / (length * length)),
    d: bends.map((p) => ((p.x - start.x) * -vy + (p.y - start.y) * vx) / length),
  };
}

/** One incoming trunk per target, immediately left of its module's column. */
function trunks(boxes: readonly RouteBox[], edges: readonly RouteEdge[], tuning: Tuning) {
  const byId = new Map(boxes.map((box) => [box.id, box]));
  const incoming = new Map<string, number[]>();
  for (const edge of edges) {
    const source = byId.get(edge.source);
    if (!source) continue;
    const ys = incoming.get(edge.target) ?? [];
    ys.push(source.anchorY);
    incoming.set(edge.target, ys);
  }
  const columns = new Map<number, RouteBox[]>();
  for (const id of new Set(edges.map((edge) => edge.target))) {
    const target = byId.get(id);
    const frame = target && byId.get(target.top);
    if (target && frame) {
      const column = columns.get(frame.x1) ?? [];
      column.push(target);
      columns.set(frame.x1, column);
    }
  }
  const lanes = new Map<string, number>();
  for (const [left, targets] of columns) {
    targets.sort((a, b) => a.anchorY - b.anchorY || (a.id < b.id ? -1 : 1));
    const previousRight = Math.max(
      -Infinity,
      ...boxes.filter((box) => box.id === box.top && box.x2 < left).map((box) => box.x2),
    );
    const room = left - previousRight;
    const inset = Math.min(tuning.loopInset, room / 3);
    const occupied: number[] = [];
    const spans = targets
      .map((target) => {
        const ys = [target.anchorY, ...(incoming.get(target.id) ?? [])];
        return { target, low: Math.min(...ys), high: Math.max(...ys) };
      })
      .sort(
        (a, b) =>
          a.low - b.low ||
          a.target.anchorY - b.target.anchorY ||
          a.target.id.localeCompare(b.target.id),
      );
    for (const { target, low, high } of spans) {
      let rank = occupied.findIndex((end) => end < low - tuning.laneGap);
      if (rank < 0) rank = occupied.length;
      occupied[rank] = high;
      lanes.set(target.id, left - inset - rank * tuning.laneGap);
    }
  }
  return lanes;
}

export function routeEdges(
  boxes: readonly RouteBox[],
  edges: readonly RouteEdge[],
  tuning: Tuning,
): Map<string, Route> {
  const byId = new Map(boxes.map((box) => [box.id, box]));
  const tops = boxes.filter((box) => box.id === box.top);
  const blocked = obstacleChecks(tops);
  const lanes = trunks(boxes, edges, tuning);
  const routes = new Map<string, Route>();
  for (const edge of edges) {
    const from = byId.get(edge.source);
    const to = byId.get(edge.target);
    if (!from || !to || from.id === to.id) continue;
    const source = byId.get(from.top);
    const lane = lanes.get(to.id);
    if (!source || lane === undefined) continue;
    const start = { x: from.x2, y: from.anchorY };
    const end = { x: to.x1, y: to.anchorY };
    // Only the short taps traverse their endpoint's own frame. The visibility search treats
    // every module, including both endpoints, as an obstacle for the rest of the path.
    const exit = { x: source.x2 + tuning.laneGap, y: start.y };
    const trunk = { x: lane, y: end.y };
    const entry = { x: lane, y: start.y };
    const path = blocked(entry, trunk)
      ? null
      : orthogonalPath(exit, entry, tops, tuning.laneGap, blocked);
    const preferred = path && simplify([start, ...path, trunk, end]);
    if (preferred && clearRoute(preferred, from, to, blocked)) {
      routes.set(edge.id, encode(preferred, from, to));
      continue;
    }
    // Crowded target trunks can spill into the preceding column. Try clear local ingress lanes
    // and let the visibility search detour before reaching them, rather than demanding a single
    // vertical span from the source's height. Keep the incumbent route whenever it is clear.
    const target = byId.get(to.top);
    if (!target) continue;
    const candidates = [
      ...new Set([
        target.x1 - tuning.laneGap,
        lane,
        ...tops.flatMap((box) => [box.x1 - tuning.laneGap, box.x2 + tuning.laneGap]),
      ]),
    ].filter((x) => x < to.x1);
    for (const x of candidates) {
      const ingress = { x, y: end.y };
      if (blocked(ingress, end, (box) => box.id === to.top)) continue;
      if (blocked(start, exit, (box) => box.id === from.top)) break;
      const alternate = orthogonalPath(exit, ingress, tops, tuning.laneGap, blocked);
      if (!alternate) continue;
      const points = simplify([start, ...alternate, end]);
      if (!clearRoute(points, from, to, blocked)) continue;
      routes.set(edge.id, encode(points, from, to));
      break;
    }
  }
  return routes;
}

/** Endpoint taps may traverse their own frame, but never any foreign frame. */
function clearRoute(
  points: XY[],
  from: RouteBox,
  to: RouteBox,
  blocked: ReturnType<typeof obstacleChecks<RouteBox>>,
): boolean {
  return points.slice(1).every((b, i) => {
    const a = points[i];
    return (
      a !== undefined &&
      !blocked(
        a,
        b,
        (box) => (i === 0 && box.id === from.top) || (i === points.length - 2 && box.id === to.top),
      )
    );
  });
}

/** The theme tokens the router and its adapter read. */
export interface RouteTheme {
  "edge-lane-gap": string;
  "edge-port-gap": string;
  "spacing-md": string;
  "spacing-xs": string;
  "frame-pad-left": string;
  "frame-header-center": string;
  "class-pad-top": string;
}

/** The router's constants in graph units, from the theme's tokens. */
export function tuningFrom(theme: RouteTheme): Tuning {
  const px = Number.parseFloat;
  return {
    laneGap: px(theme["edge-lane-gap"]),
    portGap: px(theme["edge-port-gap"]),
    portMargin: px(theme["spacing-xs"]),
    // A frame's own left padding is not a gutter between frames.
    minGutter: px(theme["frame-pad-left"]) * 2,
    loopInset: px(theme["spacing-md"]) + 2,
  };
}

interface RoutingContext {
  boxes: RouteBox[];
  calls: RouteEdge[];
  edges: EdgeCollection;
}
const contexts = new WeakMap<Core, RoutingContext>();

const ROUTE_FIELDS = ["s", "t", "w", "d"] as const;

/** All route fields are numeric arrays; avoid serializing every edge twice per frame. */
function sameRoute(a: Route | undefined, b: Route | undefined): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  for (const key of ROUTE_FIELDS) {
    if (a[key].length !== b[key].length) return false;
    for (let i = 0; i < a[key].length; i++) if (a[key][i] !== b[key][i]) return false;
  }
  return true;
}

function writeRoutes(cy: Core, edges: EdgeCollection, routes: ReadonlyMap<string, Route>) {
  cy.batch(() => {
    for (const edge of edges) {
      const route = routes.get(edge.id());
      const unroutable = !route && !edge.hasClass("imports");
      if (edge.hasClass("unroutable") !== unroutable) edge.toggleClass("unroutable", unroutable);
      if (!sameRoute(edge.data("route") as Route | undefined, route)) edge.data({ route });
    }
  });
  const container = cy.container();
  if (container && typeof CustomEvent !== "undefined")
    container.dispatchEvent(
      new CustomEvent("routingomissions", { detail: routingOmissions(edges) }),
    );
}

/** Every call still marked unroutable is disclosed, including calls within an open module. */
export function routingOmissions(edges: EdgeCollection): number {
  return edges.filter(".calls.unroutable").length;
}

/** A module drag is a rigid translation: reuse measured boxes, never remeasure SVG labels. */
export function dragRoutes(cy: Core, moduleId: string, dx: number, dy: number, theme: RouteTheme) {
  const context = contexts.get(cy);
  if (!context) return;
  for (const box of context.boxes)
    if (box.top === moduleId) {
      box.x1 += dx;
      box.x2 += dx;
      box.y1 += dy;
      box.y2 += dy;
      box.anchorY += dy;
    }
  const routes = routeEdges(context.boxes, context.calls, tuningFrom(theme));
  writeRoutes(cy, context.edges, routes);
}

/**
 * Routes the graph's call edges and stores each route in its edge's data, where the stylesheet reads
 * it. Run after the nodes are placed and again whenever one moves; it moves nothing itself.
 */
export function applyRoutes(cy: Core, theme: RouteTheme): void {
  const px = Number.parseFloat;
  const boxes: RouteBox[] = cy.nodes().map((node) => {
    const box = node.boundingBox({ includeLabels: false, includeOverlays: false });
    // Edges meet a frame with children on its header band, and anything else on its middle.
    const band = node.isChildless()
      ? undefined
      : node.hasClass("class")
        ? px(theme["class-pad-top"]) / 2
        : node.hasClass("module")
          ? px(theme["frame-header-center"])
          : undefined;
    const top = node.ancestors().last();
    return {
      id: node.id(),
      x1: box.x1,
      y1: box.y1,
      x2: box.x2,
      y2: box.y2,
      anchorY: band === undefined ? (box.y1 + box.y2) / 2 : box.y1 + band,
      top: top.nonempty() ? top.id() : node.id(),
    };
  });
  const calls = cy
    .edges()
    .toArray()
    .filter((edge) => !edge.hasClass("imports"))
    .map((edge) => ({ id: edge.id(), source: edge.source().id(), target: edge.target().id() }));
  const routes = routeEdges(boxes, calls, tuningFrom(theme));
  const edges = cy.edges();
  contexts.set(cy, { boxes, calls, edges });
  writeRoutes(cy, edges, routes);
}
