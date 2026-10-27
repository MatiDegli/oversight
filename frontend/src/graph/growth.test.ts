import { readFileSync } from "node:fs";

import type { GraphNode, GraphSnapshot } from "@contracts";
import cytoscape from "cytoscape";
import type { Core } from "cytoscape";
import fcose from "cytoscape-fcose";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createInitialState } from "../state";
import { themes } from "../theme";

import { graphElements } from "./elements";
import type { GraphElement } from "./elements";
import { graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

cytoscape.use(fcose);

const root = new URL("../../../", import.meta.url);
const snapshot = JSON.parse(
  readFileSync(new URL("fixtures/graph/demo-repo.json", root), "utf8"),
) as GraphSnapshot;
const preset = JSON.parse(
  readFileSync(new URL("fixtures/layouts/demo-repo.json", root), "utf8"),
) as GraphSnapshot["positions"];

const added = (id: string, kind: GraphNode["kind"], parent: GraphNode): GraphNode => ({
  id,
  kind,
  parent_id: parent.id,
  name: id.split(/[:.]/).at(-1) ?? id,
  qualname: id.split(":").at(-1) ?? id,
  path: parent.path,
  start_line: 1,
  end_line: 2,
  is_test: false,
});
const required = <T>(value: T | undefined): T => {
  if (value === undefined) throw new Error("Missing test value");
  return value;
};
const byId = (id: string) => required(snapshot.nodes.find((node) => node.id === id));

let cy: Core | undefined;
afterEach(() => {
  cy?.destroy();
  cy = undefined;
});

/** The demo at its saved preset, then `nodes` arrive as one graph update. */
function grow(nodes: GraphNode[], baseSnapshot = snapshot) {
  const state = createInitialState();
  const base = { ...baseSnapshot, positions: preset };
  // The graph as the app holds it: placed from its preset, with its frames settled.
  cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(themes.dark) });
  updateGraph(cy, graphElements({ ...state, graph: base }));
  const graph = cy;
  // A compound has no position of its own: its frame follows its children.
  const before = new Map(graph.nodes(":childless").map((n) => [n.id(), { ...n.position() }]));
  const boxes = () =>
    graph.nodes().map((n) => ({
      id: n.id(),
      node: n,
      box: n.boundingBox({ includeLabels: false, includeOverlays: false }),
    }));
  // A compound's box may touch the boxes of its own ancestors and descendants, never others.
  const overlaps = () => {
    const found: string[] = [];
    for (const [i, a] of boxes().entries())
      for (const b of boxes().slice(i + 1)) {
        const related = a.node.ancestors().contains(b.node) || b.node.ancestors().contains(a.node);
        const apart =
          a.box.x2 <= b.box.x1 ||
          b.box.x2 <= a.box.x1 ||
          a.box.y2 <= b.box.y1 ||
          b.box.y2 <= a.box.y1;
        if (!related && !apart) found.push(`${a.id} / ${b.id}`);
      }
    return found;
  };
  const overlapsBefore = overlaps();
  const moduleBounds = graph.getElementById("shop.orders").boundingBox({ includeLabels: true });
  updateGraph(
    graph,
    graphElements({ ...state, graph: { ...base, nodes: [...base.nodes, ...nodes] } }),
  );
  return { graph, before, moduleBounds, overlapsBefore, overlapsAfter: overlaps() };
}

describe("a class that gets its first method", () => {
  it.each(["shop.orders:Order", "shop.models:OrderLine", "shop.inventory:OutOfStock"])(
    "grows %s without overlap and restores every leaf position after rejection",
    (classId) => {
      const parent = byId(classId);
      const method = added(`${classId}.refund`, "method", parent);
      const { graph, before, overlapsBefore, overlapsAfter } = grow([method]);
      expect(overlapsBefore).toEqual([]);
      expect(overlapsAfter).toEqual([]);
      expect(graph.getElementById(method.id).parent().first().id()).toBe(classId);
      for (const [id, position] of before) {
        const node = graph.getElementById(id);
        if (node.isChildless()) expect(node.position()).toEqual(position);
      }
      updateGraph(
        graph,
        graphElements({ ...createInitialState(), graph: { ...snapshot, positions: preset } }),
      );
      for (const [id, position] of before)
        expect(graph.getElementById(id).position()).toEqual(position);
    },
  );

  it("places Order's first method below its module when there is room without overlap or moving existing leaves", () => {
    const parent = byId("shop.orders:Order");
    const method = added(`${parent.id}.refund`, "method", parent);
    // Keep the module's tight preset, but leave its surroundings free for the direction choice.
    const orders = {
      ...snapshot,
      nodes: snapshot.nodes.filter(
        (node) => node.id === parent.parent_id || node.parent_id === parent.parent_id,
      ),
      edges: [],
    };
    const { graph, before, moduleBounds, overlapsAfter } = grow([method], orders);
    const box = graph.getElementById(method.id).boundingBox({ includeLabels: true });
    expect(box.y1).toBeGreaterThan(moduleBounds.y2);
    expect(overlapsAfter).toEqual([]);
    for (const [id, position] of before) {
      const node = graph.getElementById(id);
      if (node.isChildless()) expect(node.position()).toEqual(position);
    }
  });

  it("restores a dragged leaf position through repeated growth and rejection without a preset", () => {
    const parent = byId("shop.orders:Order");
    const method = added(`${parent.id}.refund`, "method", parent);
    const { graph } = grow([method]);
    const base = graphElements({ ...createInitialState(), graph: snapshot });
    const withMethod = graphElements({
      ...createInitialState(),
      graph: { ...snapshot, nodes: [...snapshot.nodes, method] },
    });
    updateGraph(graph, base);
    const dragged = { x: 731, y: 80 };
    graph.getElementById(parent.id).position(dragged);
    for (let round = 0; round < 2; round++) {
      updateGraph(graph, withMethod);
      updateGraph(graph, base);
      expect(graph.getElementById(parent.id).position()).toEqual(dragged);
    }
  });

  it("keeps clear of every sibling when two methods arrive in one update", () => {
    const parent = byId("shop.orders:Order");
    const { overlapsAfter, graph } = grow([
      added("shop.orders:Order.refund", "method", parent),
      added("shop.orders:Order.void", "method", parent),
    ]);
    expect(overlapsAfter).toEqual([]);
    expect(graph.getElementById("shop.orders:Order").children().length).toBe(2);
  });
});

describe("the box of a new node", () => {
  it.each(
    ["shop.orders", "shop.models", "shop.inventory", "shop.cart", "shop.pricing"].flatMap((id) => [
      { id, withMethod: false },
      { id, withMethod: true },
    ]),
  )(
    "adds a new class to $id without stretching its module over other frames (method: $withMethod)",
    ({ id, withMethod }) => {
      const parent = byId(id);
      const newClass = added(`${id}:NewClass`, "class", parent);
      const { graph, before, overlapsAfter } = grow([
        newClass,
        ...(withMethod ? [added(`${id}:NewClass.method`, "method", newClass)] : []),
      ]);
      expect(overlapsAfter).toEqual([]);
      for (const [nodeId, point] of before) {
        const node = graph.getElementById(nodeId);
        if (
          id !== "shop.pricing" ||
          !withMethod ||
          !node
            .ancestors()
            .map((ancestor) => ancestor.id())
            .includes(id)
        )
          expect(node.position()).toEqual(point);
      }
      if (id === "shop.pricing" && withMethod) {
        // On the wide canvas pricing may have room beside it. Where it has none, only that module
        // may shift, as a unit (the hemmed-in test below forces it).
        const shifts = [...before]
          .filter(([nodeId]) =>
            graph
              .getElementById(nodeId)
              .ancestors()
              .map((ancestor) => ancestor.id())
              .includes(id),
          )
          .map(([nodeId, point]) => graph.getElementById(nodeId).position().y - point.y);
        expect(new Set(shifts).size).toBe(1);
        expect(shifts[0]).toBeGreaterThanOrEqual(0);
      }
    },
  );

  it("restores pricing's pre-shift leaves after nested growth is rejected, repeatedly without a preset", () => {
    const parent = byId("shop.pricing");
    const newClass = added(`${parent.id}:NewClass`, "class", parent);
    const method = added(`${newClass.id}.method`, "method", newClass);
    const { graph, before, overlapsAfter } = grow([newClass, method]);
    expect(overlapsAfter).toEqual([]);
    const base = graphElements({ ...createInitialState(), graph: snapshot });
    const grown = graphElements({
      ...createInitialState(),
      graph: { ...snapshot, nodes: [...snapshot.nodes, newClass, method] },
    });
    // Intermediate events must not replace the saved pre-shift positions.
    updateGraph(graph, grown);
    updateGraph(graph, base);
    for (const [id, position] of before)
      expect(graph.getElementById(id).position(), id).toEqual(position);
    updateGraph(graph, grown);
    updateGraph(graph, base);
    for (const [id, position] of before)
      expect(graph.getElementById(id).position(), id).toEqual(position);
  });

  it("tries the sides before above when the space below a module is blocked", () => {
    const base: GraphElement[] = [
      {
        group: "nodes",
        data: { id: "module", kind: "module", label: "module" },
        classes: "module",
      },
      {
        group: "nodes",
        data: { id: "existing", parent: "module", kind: "function", label: "existing" },
        classes: "function",
        position: { x: 0, y: 0 },
      },
      {
        group: "nodes",
        data: { id: "below", kind: "module", label: "below" },
        classes: "module",
        position: { x: 0, y: 100 },
      },
    ];
    cy = cytoscape({
      headless: true,
      styleEnabled: true,
      style: [
        ...graphStyles(themes.dark),
        { selector: "#below", style: { width: 2000, height: 100 } },
      ],
      elements: base,
      layout: { name: "preset" },
    });
    const boundary = cy.getElementById("module").boundingBox({ includeLabels: true });
    updateGraph(cy, [
      ...base,
      {
        group: "nodes",
        data: { id: "new", parent: "module", kind: "class", label: "new" },
        classes: "class",
      },
    ]);
    const box = cy.getElementById("new").boundingBox({ includeLabels: false });
    expect(box.x1 > boundary.x2 || box.x2 < boundary.x1).toBe(true);
    expect(box.y1).toBeLessThan(boundary.y2);
    expect(box.y2).toBeGreaterThan(boundary.y1);
  });

  it("moves a hemmed-in module as a unit to free space below the graph, and brings it back", () => {
    const node = (id: string, kind: "module" | "function", x: number, y: number, parent?: string) =>
      ({
        group: "nodes",
        data: { id, kind, label: id, ...(parent ? { parent } : {}) },
        classes: kind,
        position: { x, y },
      }) satisfies GraphElement;
    // Four walls a few px from the module's frame, each far wider than the search's rings.
    const base: GraphElement[] = [
      { group: "nodes", data: { id: "m", kind: "module", label: "m" }, classes: "module" },
      node("f", "function", 0, 0, "m"),
      node("g", "function", 0, 57, "m"),
      node("left", "module", -2200, 0),
      node("right", "module", 2200, 0),
      node("top", "module", 0, -2200),
      node("bottom", "module", 0, 2200),
    ];
    cy = cytoscape({
      headless: true,
      styleEnabled: true,
      style: [
        ...graphStyles(themes.dark),
        { selector: "#left, #right", style: { width: 4000, height: 8000 } },
        { selector: "#top, #bottom", style: { width: 8000, height: 4000 } },
      ],
    });
    const graph = cy;
    updateGraph(graph, base);
    // Walls fitted to the frame the module has now, 8px apart.
    const frame = graph.getElementById("m").boundingBox({ includeLabels: false });
    graph.getElementById("left").position({ x: frame.x1 - 8 - 2000, y: 0 });
    graph.getElementById("right").position({ x: frame.x2 + 8 + 2000, y: 0 });
    graph.getElementById("top").position({ x: 0, y: frame.y1 - 8 - 2000 });
    graph.getElementById("bottom").position({ x: 0, y: frame.y2 + 8 + 2000 });
    const before = new Map(graph.nodes(":childless").map((n) => [n.id(), { ...n.position() }]));
    const graphBottom = graph.nodes().boundingBox({ includeLabels: false }).y2;
    const withClass: GraphElement[] = [
      ...base,
      {
        group: "nodes",
        data: { id: "c", kind: "class", label: "c", parent: "m" },
        classes: "class",
      },
    ];
    updateGraph(graph, withClass);
    // The class lands below the walls, and the module's rows go with it by one and the same shift.
    expect(graph.getElementById("c").boundingBox({ includeLabels: false }).y1).toBeGreaterThan(
      graphBottom,
    );
    const shifts = ["f", "g"].map(
      (id) => graph.getElementById(id).position().y - required(before.get(id)).y,
    );
    expect(shifts[0]).toBeGreaterThan(0);
    expect(shifts[1]).toBe(shifts[0]);
    for (const id of ["left", "right", "top", "bottom"])
      expect(graph.getElementById(id).position()).toEqual(required(before.get(id)));
    // The class is withdrawn: every node goes back to where it was.
    updateGraph(graph, base);
    for (const [id, point] of before)
      expect(graph.getElementById(id).position(), id).toEqual(point);
  });

  it("uses a free spot below the graph when all 100 rings are blocked", () => {
    const node = (
      id: string,
      kind: "class" | "module" | "function",
      x: number,
      y: number,
    ): GraphElement => ({
      group: "nodes",
      data: { id, kind, label: id },
      classes: kind,
      position: { x, y },
    });
    const base = [
      node("class", "class", 0, 0),
      node("sibling", "function", 0, 100),
      node("left", "module", -1590, 0),
      node("right", "module", 1590, 0),
      node("top", "module", 0, -190),
    ];
    cy = cytoscape({
      headless: true,
      styleEnabled: true,
      style: [
        ...graphStyles(themes.dark),
        { selector: "#left, #right", style: { width: 3000, height: 6000 } },
        { selector: "#top", style: { width: 6180, height: 200 } },
      ],
      elements: base,
      layout: { name: "preset" },
    });
    const before = new Map(cy.nodes().map((n) => [n.id(), { ...n.position() }]));
    const boundary = cy.nodes().boundingBox({ includeLabels: true });
    updateGraph(cy, [
      ...base,
      {
        group: "nodes",
        data: { id: "method", kind: "method", parent: "class", label: "method" },
        classes: "method",
      },
    ]);
    for (const id of ["class", "method"]) {
      const box = cy.getElementById(id).boundingBox({ includeLabels: true });
      // Touching the graph's bottom edge is free space: nothing is under it.
      expect(box.y1).toBeGreaterThanOrEqual(boundary.y2);
      for (const otherId of ["sibling", "left", "right", "top"]) {
        const other = cy.getElementById(otherId).boundingBox({ includeLabels: true });
        expect(
          box.x1 >= other.x2 || box.x2 <= other.x1 || box.y1 >= other.y2 || box.y2 <= other.y1,
        ).toBe(true);
      }
    }
    for (const [id, position] of before)
      if (id !== "class") expect(cy.getElementById(id).position()).toEqual(position);
    // The method is withdrawn: the class goes back to the place it had.
    updateGraph(cy, base);
    for (const [id, position] of before)
      expect(cy.getElementById(id).position(), id).toEqual(position);
  });

  it("comes from the theme's tokens, so larger rows keep larger nodes apart", () => {
    const large = { ...themes.dark, "node-width": "400px", "node-height": "90px" };
    cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(large) });
    // The layout that follows the first update moves nodes it was handed; keep a copy of them.
    let handed: GraphElement[] = [];
    const add = cy.add.bind(cy);
    vi.spyOn(cy, "add").mockImplementation((elements) => {
      handed = structuredClone(elements) as GraphElement[];
      return add(elements);
    });
    const node = (id: string): GraphElement => ({
      group: "nodes",
      data: { id, kind: "function" },
      classes: "function",
    });
    updateGraph(cy, [node("a"), node("b"), node("c")], large);
    const points = handed.map((element) => required(element.position));
    expect(points).toHaveLength(3);
    // A row of 400x90 takes that much room: no two of them may overlap.
    for (const [i, p] of points.entries())
      for (const q of points.slice(i + 1))
        expect(Math.abs(p.x - q.x) >= 400 || Math.abs(p.y - q.y) >= 90, String(i)).toBe(true);
  });
});
