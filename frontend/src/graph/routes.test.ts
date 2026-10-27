import cytoscape from "cytoscape";
import { afterEach, describe, expect, it } from "vitest";

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
  it("leaves a caller's right border and enters its callee's left border, through the gutter", () => {
    const { line, from, to } = route([["e", "a:f", "b:h"]])("e");
    expect(orthogonal(line)).toBe(true);
    expect(line[0]?.x).toBe(from.x2);
    expect(line.at(-1)?.x).toBe(to.x1);
    // The vertical run sits in the gap between the two frames, not over either.
    const lane = line[1]?.x ?? 0;
    expect(lane).toBeGreaterThan(a.x2);
    expect(lane).toBeLessThan(b.x1);
  });

  it("draws a call between two rows at one height as one straight line", () => {
    const level = row("b:level", 500, 100, "b");
    const { found, line } = route(
      [["e", "a:f", "b:level"]],
      [a, a1, a2, frame("b", [level]), level],
    )("e");
    expect(found.w).toEqual([]);
    expect(line).toHaveLength(2);
    expect(line[0]?.y).toBe(line[1]?.y);
  });

  it("leaves a callee at the left by the caller's left border and enters it at the right", () => {
    const { line, from, to } = route([["e", "b:h", "a:f"]])("e");
    expect(orthogonal(line)).toBe(true);
    expect(line[0]?.x).toBe(from.x1);
    expect(line.at(-1)?.x).toBe(to.x2);
    const lane = line[1]?.x ?? 0;
    expect(lane).toBeGreaterThan(a.x2);
    expect(lane).toBeLessThan(b.x1);
  });

  it("gives each call into one gutter its own lane, at least the lane gap apart", () => {
    const calls = route([
      ["one", "a:f", "b:h"],
      ["two", "a:g", "b:i"],
      ["three", "a:g", "b:h"],
    ]);
    const lanes = ["one", "two", "three"].map((id) => calls(id).line[1]?.x ?? 0);
    expect(new Set(lanes).size).toBe(3);
    const sorted = [...lanes].sort((p, q) => p - q);
    for (const [i, lane] of sorted.entries())
      if (i > 0) expect(lane - (sorted[i - 1] ?? 0)).toBeGreaterThanOrEqual(tuning.laneGap - 1e-6);
    for (const id of ["one", "two", "three"]) expect(orthogonal(calls(id).line)).toBe(true);
  });

  it("spreads the calls that meet one side of a row along its height, inside its border", () => {
    const calls = route([
      ["one", "a:f", "b:h"],
      ["two", "a:g", "b:h"],
      ["three", "b:i", "b:h"],
    ]);
    // Into b:h's left side: the first two from the left; the loop from b:i also joins at the left.
    const ys = ["one", "two", "three"].map((id) => calls(id).line.at(-1)?.y ?? 0);
    expect(new Set(ys).size).toBe(3);
    for (const y of ys) {
      expect(y).toBeGreaterThan(b1.y1);
      expect(y).toBeLessThan(b1.y2);
    }
  });

  it("loops a call between two rows of one module through a lane at the left, inside its frame", () => {
    const { line, from, to } = route([["e", "a:g", "a:f"]])("e");
    expect(orthogonal(line)).toBe(true);
    expect(line[0]?.x).toBe(from.x1);
    expect(line.at(-1)?.x).toBe(to.x1);
    const lane = line[1]?.x ?? 0;
    expect(lane).toBeGreaterThan(a.x1);
    expect(lane).toBeLessThan(a1.x1);
  });

  it("runs a call between two modules in one column through a lane at the left of both", () => {
    const c1 = row("c:k", 27, 400, "c");
    const c = frame("c", [c1]);
    const { line } = route([["e", "a:f", "c:k"]], [a, a1, a2, c, c1])("e");
    expect(orthogonal(line)).toBe(true);
    expect(line[1]?.x).toBeLessThan(Math.min(a.x1, c.x1));
  });

  it("routes nothing for a self call or an end it cannot find", () => {
    const routes = routeEdges(
      boxes,
      [
        { id: "self", source: "a:f", target: "a:f" },
        { id: "ghost", source: "a:f", target: "nowhere" },
      ],
      tuning,
    );
    expect(routes.size).toBe(0);
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
