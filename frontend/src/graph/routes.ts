// Orthogonal ("taxi") edge routes through the gutters between module frames, as in the Signal
// sketch: a call leaves its caller's row at the right, runs along a lane in the gutter and enters
// its callee's row at the left, with the arrowhead on the border. A call between two rows of one
// module, or of two modules in one column, loops through a lane at the left instead.
//
// The router is a pure function of boxes and edges. It places no node: it only says where each edge
// meets its two nodes (offsets from the node's centre, in px) and where it bends, in the terms
// Cytoscape's `segments` style takes (weights along the line between the two ends, distances
// across it), which the stylesheet reads from the edge's data.
import type { Core } from "cytoscape";

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

type Kind = "forward" | "backward" | "loop" | "stacked";

interface Plan {
  edge: RouteEdge;
  from: RouteBox;
  to: RouteBox;
  kind: Kind;
  /** Which way the vertical run goes: the target is below the source (or level with it). */
  down: boolean;
  lane: number;
  fromY: number;
  toY: number;
}

const center = (box: RouteBox) => ({ x: (box.x1 + box.x2) / 2, y: (box.y1 + box.y2) / 2 });

/** The gaps between the outermost frames: runs of x no frame covers, as [from, to]. */
function corridors(tops: readonly RouteBox[], minGutter: number): [number, number][] {
  const sorted = [...tops].sort((a, b) => a.x1 - b.x1 || a.x2 - b.x2);
  const gaps: [number, number][] = [];
  let reach = sorted[0]?.x2 ?? 0;
  for (const box of sorted.slice(1)) {
    if (box.x1 - reach >= minGutter) gaps.push([reach, box.x1]);
    reach = Math.max(reach, box.x2);
  }
  return gaps;
}

/** How many frames, other than the two ends', a horizontal run from `xa` to `xb` at `y` crosses. */
function crossings(
  tops: readonly RouteBox[],
  skip: readonly string[],
  xa: number,
  xb: number,
  y: number,
) {
  const [lo, hi] = [Math.min(xa, xb), Math.max(xa, xb)];
  return tops.filter(
    (box) => !skip.includes(box.id) && box.y1 < y && y < box.y2 && box.x1 < hi && box.x2 > lo,
  ).length;
}

/** Evenly spaced offsets around zero, at most `step` apart and inside ±`limit`. */
function spread(count: number, step: number, limit: number): number[] {
  const gap = count > 1 ? Math.min(step, (2 * limit) / (count - 1)) : 0;
  return Array.from({ length: count }, (_, k) => (k - (count - 1) / 2) * gap);
}

export function routeEdges(
  boxes: readonly RouteBox[],
  edges: readonly RouteEdge[],
  tuning: Tuning,
): Map<string, Route> {
  const byId = new Map(boxes.map((box) => [box.id, box]));
  const tops = boxes.filter((box) => box.id === box.top);
  const gutters = corridors(tops, tuning.minGutter);
  const plans: Plan[] = [];
  for (const edge of edges) {
    const from = byId.get(edge.source);
    const to = byId.get(edge.target);
    if (!from || !to || from.id === to.id) continue;
    const a = byId.get(from.top);
    const b = byId.get(to.top);
    if (!a || !b) continue;
    const kind: Kind =
      a.id === b.id ? "loop" : b.x1 >= a.x2 ? "forward" : b.x2 <= a.x1 ? "backward" : "stacked";
    plans.push({
      edge,
      from,
      to,
      kind,
      down: to.anchorY >= from.anchorY,
      lane: 0,
      fromY: from.anchorY,
      toY: to.anchorY,
    });
  }

  // Lanes in the gutters. A call picks the gutter that makes its two horizontal runs cross the
  // fewest frames, the one nearest its callee when several do equally well.
  const chosen = new Map<string, Plan[]>();
  for (const plan of plans) {
    if (plan.kind !== "forward" && plan.kind !== "backward") continue;
    const a = byId.get(plan.from.top);
    const b = byId.get(plan.to.top);
    if (!a || !b) continue;
    const [lo, hi] = plan.kind === "forward" ? [a.x2, b.x1] : [b.x2, a.x1];
    const between = gutters.filter(([from, to]) => from >= lo - 1 && to <= hi + 1);
    const start = plan.kind === "forward" ? plan.from.x2 : plan.from.x1;
    const end = plan.kind === "forward" ? plan.to.x1 : plan.to.x2;
    const options = between.length ? between : ([[lo, hi]] as [number, number][]);
    let best = options[0] ?? [lo, hi];
    let bestCost = Infinity;
    for (const gutter of options) {
      const x = (gutter[0] + gutter[1]) / 2;
      const cost =
        crossings(tops, [a.id, b.id], start, x, plan.fromY) +
        crossings(tops, [a.id, b.id], x, end, plan.toY);
      const nearer = plan.kind === "forward" ? gutter[0] > best[0] : gutter[0] < best[0];
      if (cost < bestCost || (cost === bestCost && nearer)) {
        best = gutter;
        bestCost = cost;
      }
    }
    const key = `${String(best[0])}:${String(best[1])}`;
    chosen.set(key, [...(chosen.get(key) ?? []), plan]);
  }
  for (const [key, group] of chosen) {
    const [from = 0, to = 0] = key.split(":").map(Number);
    const forward = group.filter((p) => p.kind === "forward");
    const backward = group.filter((p) => p.kind === "backward");
    // Inner lanes (nearer the caller) go to the edges that start lower when they run down, and to
    // the ones that start higher when they run up, so a run never cuts through another's lane.
    const order = (plan: Plan, other: Plan) =>
      Number(other.down) - Number(plan.down) ||
      (plan.down
        ? other.fromY - plan.fromY || other.toY - plan.toY
        : plan.fromY - other.fromY || plan.toY - other.toY) ||
      (plan.edge.id < other.edge.id ? -1 : 1);
    forward.sort(order);
    backward.sort(order);
    const count = group.length;
    const gap = Math.min(tuning.laneGap, (to - from) / (count + 1));
    const left = (from + to) / 2 - ((count - 1) * gap) / 2;
    forward.forEach((plan, i) => (plan.lane = left + i * gap));
    backward.forEach((plan, i) => (plan.lane = left + (count - 1 - i) * gap));
  }

  // Loops at the left: the shorter a loop, the nearer its lane to the rows it joins.
  const loops = new Map<string, Plan[]>();
  for (const plan of plans) {
    if (plan.kind !== "loop" && plan.kind !== "stacked") continue;
    const key = plan.kind === "loop" ? plan.from.top : `${plan.from.top}|${plan.to.top}`;
    loops.set(key, [...(loops.get(key) ?? []), plan]);
  }
  for (const group of loops.values()) {
    group.sort(
      (p, q) =>
        Math.abs(p.toY - p.fromY) - Math.abs(q.toY - q.fromY) ||
        p.fromY - q.fromY ||
        (p.edge.id < q.edge.id ? -1 : 1),
    );
    group.forEach((plan, rank) => {
      const rows = Math.min(plan.from.x1, plan.to.x1);
      if (plan.kind === "loop") {
        // Inside the module's own padding: each further loop steps half a lane nearer its border,
        // and none leaves the frame.
        const frame = byId.get(plan.from.top)?.x1 ?? rows;
        plan.lane = Math.max(
          frame + tuning.loopInset / 2,
          rows - tuning.loopInset - (rank * tuning.laneGap) / 2,
        );
      } else {
        // In the gutter at the left of both modules.
        const frames = Math.min(
          byId.get(plan.from.top)?.x1 ?? rows,
          byId.get(plan.to.top)?.x1 ?? rows,
        );
        plan.lane = frames - tuning.loopInset - rank * tuning.laneGap;
      }
    });
  }

  // Where each edge meets its nodes: on one side of a node, edges are sorted by the height of the
  // other end, so that those to and from above meet the upper part and the rest the lower part.
  const sideOf = (plan: Plan, end: "from" | "to") =>
    plan.kind === "forward"
      ? end === "from"
        ? "right"
        : "left"
      : plan.kind === "backward"
        ? end === "from"
          ? "left"
          : "right"
        : "left";
  const ports = new Map<string, { plan: Plan; end: "from" | "to" }[]>();
  for (const plan of plans)
    for (const end of ["from", "to"] as const) {
      const key = `${plan[end].id}|${sideOf(plan, end)}`;
      ports.set(key, [...(ports.get(key) ?? []), { plan, end }]);
    }
  const other = (entry: { plan: Plan; end: "from" | "to" }) =>
    entry.end === "from" ? entry.plan.to.anchorY : entry.plan.from.anchorY;
  for (const entries of ports.values()) {
    entries.sort(
      (p, q) =>
        other(p) - other(q) ||
        Math.abs(p.plan.lane) - Math.abs(q.plan.lane) ||
        (p.plan.edge.id < q.plan.edge.id ? -1 : 1),
    );
    const node = entries[0]?.plan[entries[0].end];
    if (!node) continue;
    const height = node.y2 - node.y1;
    const offsets = spread(
      entries.length,
      tuning.portGap,
      Math.min(Math.max(0, height / 2 - tuning.portMargin), (entries.length * tuning.portGap) / 2),
    );
    entries.forEach(({ plan, end }, i) => {
      const y = plan[end].anchorY + (offsets[i] ?? 0);
      if (end === "from") plan.fromY = y;
      else plan.toY = y;
    });
  }

  const routes = new Map<string, Route>();
  for (const plan of plans) {
    const start = {
      x: sideOf(plan, "from") === "right" ? plan.from.x2 : plan.from.x1,
      y: plan.fromY,
    };
    const end = { x: sideOf(plan, "to") === "right" ? plan.to.x2 : plan.to.x1, y: plan.toY };
    const bends = [
      { x: plan.lane, y: start.y },
      { x: plan.lane, y: end.y },
    ].filter((p) => Math.abs(p.y - start.y) + Math.abs(p.y - end.y) > 0);
    const vx = end.x - start.x;
    const vy = end.y - start.y;
    const length = Math.hypot(vx, vy);
    if (length === 0) continue;
    const straight = start.y === end.y;
    const w: number[] = [];
    const d: number[] = [];
    if (!straight)
      for (const p of bends) {
        // Cytoscape puts a bend at the end-to-end line's weight w, offset d along its left normal.
        w.push(((p.x - start.x) * vx + (p.y - start.y) * vy) / (length * length));
        d.push(((p.x - start.x) * -vy + (p.y - start.y) * vx) / length);
      }
    const from = center(plan.from);
    const to = center(plan.to);
    routes.set(plan.edge.id, {
      s: [start.x - from.x, start.y - from.y],
      t: [end.x - to.x, end.y - to.y],
      w,
      d,
    });
  }
  return routes;
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
  cy.batch(() => {
    for (const edge of cy.edges()) {
      const route = routes.get(edge.id());
      if (route) edge.data("route", route);
      else if (edge.data("route") !== undefined) edge.removeData("route");
    }
  });
}
