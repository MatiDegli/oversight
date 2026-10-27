import cytoscape from "cytoscape";
import { afterEach, describe, expect, it } from "vitest";

import { crosses } from "./orthogonal";

import { themes } from "../theme";

import { applyRoutes, routeEdges, tuningFrom } from "./routes";
import type { Route, RouteBox } from "./routes";

const tuning = tuningFrom(themes.dark);

/** A row of a frame: 282x46, its middle at `y`, in the frame `top` (or none). */
const row = (id: string, x1: number, y: number, top: string): RouteBox => ({
  id,
  x1,
  y1: y - 23,
  x2: x1 + 282,
  y2: y + 23,
  anchorY: y,
  top,
});
/** A frame around rows, with the sketch's paddings: 27 left, 54 above, 17 right and below. */
const frame = (id: string, rows: RouteBox[]): RouteBox => ({
  id,
  x1: Math.min(...rows.map((r) => r.x1)) - 27,
  y1: Math.min(...rows.map((r) => r.y1)) - 54,
  x2: Math.max(...rows.map((r) => r.x2)) + 17,
  y2: Math.max(...rows.map((r) => r.y2)) + 17,
  anchorY: Math.min(...rows.map((r) => r.y1)) - 54 + 29,
  top: id,
});
const middle = (box: RouteBox) => ({ x: (box.x1 + box.x2) / 2, y: (box.y1 + box.y2) / 2 });

/** The corners an edge makes, rebuilt from its route the way Cytoscape reads it. */
function polyline(route: Route, from: RouteBox, to: RouteBox) {
  const a = middle(from);
  const b = middle(to);
  const start = { x: a.x + route.s[0], y: a.y + route.s[1] };
  const end = { x: b.x + route.t[0], y: b.y + route.t[1] };
  const vx = end.x - start.x;
  const vy = end.y - start.y;
  const length = Math.hypot(vx, vy);
  const bends = route.w.map((w, i) => {
    const d = route.d[i] ?? 0;
    return {
      x: start.x + w * vx + (d * -vy) / length,
      y: start.y + w * vy + (d * vx) / length,
    };
  });
  return [start, ...bends, end];
}

/** Every segment of the line runs straight across or straight down. */
const orthogonal = (points: { x: number; y: number }[]) =>
  points
    .slice(1)
    .every(
      (p, i) =>
        Math.abs(p.x - (points[i]?.x ?? 0)) < 1e-6 || Math.abs(p.y - (points[i]?.y ?? 0)) < 1e-6,
    );

// Two modules side by side, 140px apart: pricing at the left and a callee at the right.
const a1 = row("a:f", 27, 100, "a");
const a2 = row("a:g", 27, 157, "a");
const b1 = row("b:h", 500, 240, "b");
const b2 = row("b:i", 500, 297, "b");
const a = frame("a", [a1, a2]);
const b = frame("b", [b1, b2]);
const boxes = [a, a1, a2, b, b1, b2];

function route(edges: [string, string, string][], list = boxes) {
  const routes = routeEdges(
    list,
    edges.map(([id, source, target]) => ({ id, source, target })),
    tuning,
  );
  const byId = new Map(list.map((box) => [box.id, box]));
  return (id: string) => {
    const edge = edges.find(([name]) => name === id);
    const found = routes.get(id);
    if (!edge || !found) throw new Error(`No route for ${id}`);
    const from = byId.get(edge[1]);
    const to = byId.get(edge[2]);
    if (!from || !to) throw new Error("Missing box");
    return { found, line: polyline(found, from, to), from, to };
  };
}

describe("routeEdges", () => {
  it("leaves right and enters left on forward, back, internal and stacked calls", () => {
    const c1 = row("c:k", 27, 400, "c");
    const c = frame("c", [c1]);
    for (const [source, target] of [
      ["a:f", "b:h"],
      ["b:h", "a:f"],
      ["a:g", "a:f"],
      ["a:f", "c:k"],
    ] as const) {
      const { line, from, to } = route([["e", source, target]], [...boxes, c, c1])("e");
      expect(orthogonal(line)).toBe(true);
      expect(line[0]?.x).toBe(from.x2);
      expect(line[1]?.x).toBeGreaterThan(from.x2);
      expect(line.at(-1)?.x).toBe(to.x1);
      expect(line.at(-2)?.x).toBeLessThan(to.x1);
      // Apart from the endpoint taps, even the endpoint frames are obstacles.
      for (let i = 1; i < line.length - 2; i++)
        for (const obstacle of [a, b, c])
          expect(
            crosses(line[i] ?? { x: 0, y: 0 }, line[i + 1] ?? { x: 0, y: 0 }, obstacle),
            `${source}>${target} crosses ${obstacle.id}`,
          ).toBe(false);
    }
  });

  it("merges eight callers into one target trunk and one target port", () => {
    const callers = Array.from({ length: 8 }, (_, i) =>
      row(`a:${String(i)}`, 27, 100 + i * 57, "a"),
    );
    const calls = route(
      callers.map((r, i) => [String(i), r.id, "b:h"]),
      [frame("a", callers), ...callers, b, b1, b2],
    );
    const lines = callers.map((_, i) => calls(String(i)).line);
    expect(new Set(lines.map((line) => line.at(-2)?.x.toFixed(6))).size).toBe(1);
    expect(new Set(lines.map((line) => line.at(-1)?.y)).size).toBe(1);
    expect(lines[0]?.at(-2)?.x).toBeLessThan(b.x1);
    expect(lines[0]?.at(-2)?.x).toBeGreaterThan(a.x2);
    for (const line of lines) expect(orthogonal(line)).toBe(true);
  });

  it("assigns different targets their own trunks", () => {
    const calls = route([
      ["one", "a:f", "b:h"],
      ["two", "a:g", "b:i"],
      ["three", "a:g", "b:h"],
    ]);
    expect(calls("one").line.at(-2)?.x).toBeCloseTo(calls("three").line.at(-2)?.x ?? 0);
    expect(
      Math.abs((calls("one").line.at(-2)?.x ?? 0) - (calls("two").line.at(-2)?.x ?? 0)),
    ).toBeGreaterThanOrEqual(tuning.laneGap - 1e-6);
  });

  it("detours around a frame in a skipped column instead of cutting its header or interior", () => {
    const far = row("far:h", 1000, 300, "far");
    const obstacle = { ...b, y1: 0, y2: 500 };
    const { line } = route(
      [["e", "a:f", "far:h"]],
      [a, a1, a2, obstacle, frame("far", [far]), far],
    )("e");
    expect(orthogonal(line)).toBe(true);
    for (let i = 0; i < line.length - 1; i++)
      expect(crosses(line[i] ?? { x: 0, y: 0 }, line[i + 1] ?? { x: 0, y: 0 }, obstacle)).toBe(
        false,
      );
  });

  it("is independent of edge input order", () => {
    const edges = [
      { id: "one", source: "a:f", target: "b:h" },
      { id: "two", source: "a:g", target: "b:i" },
    ];
    const one = routeEdges(boxes, edges, tuning);
    const two = routeEdges(boxes, [...edges].reverse(), tuning);
    for (const [id, value] of one) expect(two.get(id)).toEqual(value);
  });

  it("routes nothing for a self call or an end it cannot find", () => {
    expect(
      routeEdges(
        boxes,
        [
          { id: "self", source: "a:f", target: "a:f" },
          { id: "ghost", source: "a:f", target: "nowhere" },
        ],
        tuning,
      ).size,
    ).toBe(0);
  });
});

describe("applyRoutes", () => {
  let cy: cytoscape.Core | undefined;
  afterEach(() => {
    cy?.destroy();
    cy = undefined;
  });

  it("stores a route in each call's data, none in an import's, and drops a stale one", () => {
    cy = cytoscape({
      headless: true,
      styleEnabled: true,
      style: [{ selector: "node", style: { width: 282, height: 46, shape: "rectangle" } }],
      elements: [
        { data: { id: "m1" }, classes: "module" },
        { data: { id: "m2" }, classes: "module" },
        { data: { id: "x", parent: "m1" }, position: { x: 0, y: 0 }, classes: "function" },
        { data: { id: "y", parent: "m2" }, position: { x: 600, y: 120 }, classes: "function" },
        { data: { id: "call", source: "x", target: "y" }, classes: "calls" },
        { data: { id: "use", source: "m1", target: "m2" }, classes: "imports" },
      ],
      layout: { name: "preset" },
    });
    cy.getElementById("use").data("route", { s: [0, 0], t: [0, 0], w: [], d: [] });
    applyRoutes(cy, themes.dark);
    const stored = cy.getElementById("call").data("route") as Route | undefined;
    expect(stored?.s).toHaveLength(2);
    expect(cy.getElementById("use").data("route")).toBeUndefined();
    // The call goes away from the data when its end does.
    cy.getElementById("y").remove();
    cy.add({ data: { id: "call2", source: "x", target: "x" }, classes: "calls" });
    cy.getElementById("call2").data("route", { s: [0, 0], t: [0, 0], w: [], d: [] });
    applyRoutes(cy, themes.dark);
    expect(cy.getElementById("call2").data("route")).toBeUndefined();
  });
});
