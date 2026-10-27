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
  cy = cytoscape({
    headless: true,
    styleEnabled: true,
    style: graphStyles(themes.dark),
    elements: graphElements({ ...state, graph: base }),
    layout: { name: "preset" },
  });
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
      expect(box.y1).toBeGreaterThan(boundary.y2);
      for (const otherId of ["sibling", "left", "right", "top"]) {
        const other = cy.getElementById(otherId).boundingBox({ includeLabels: true });
        expect(
          box.x1 >= other.x2 || box.x2 <= other.x1 || box.y1 >= other.y2 || box.y2 <= other.y1,
        ).toBe(true);
      }
    }
    for (const [id, position] of before)
      if (id !== "class") expect(cy.getElementById(id).position()).toEqual(position);
  });

  it("comes from the theme's tokens, so a larger body type keeps larger nodes apart", () => {
    const large = { ...themes.dark, "body-size": "30px" };
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
    // A leaf at 30px body type is 300x120 plus its border: no two of them may overlap.
    for (const [i, p] of points.entries())
      for (const q of points.slice(i + 1))
        expect(Math.abs(p.x - q.x) >= 301 || Math.abs(p.y - q.y) >= 121, String(i)).toBe(true);
  });
});
