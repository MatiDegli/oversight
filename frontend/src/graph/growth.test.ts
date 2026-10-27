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
function grow(nodes: GraphNode[]) {
  const state = createInitialState();
  const base = { ...snapshot, positions: preset };
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
  updateGraph(
    graph,
    graphElements({ ...state, graph: { ...base, nodes: [...base.nodes, ...nodes] } }),
  );
  return { graph, before, overlapsBefore, overlapsAfter: overlaps() };
}

describe("a class that gets its first method", () => {
  it.each(["shop.orders:Order", "shop.models:OrderLine", "shop.inventory:OutOfStock"])(
    "grows %s without covering a sibling or moving an existing node",
    (classId) => {
      const parent = byId(classId);
      const method = added(`${classId}.refund`, "method", parent);
      const { graph, before, overlapsBefore, overlapsAfter } = grow([method]);
      expect(overlapsBefore).toEqual([]);
      expect(overlapsAfter).toEqual([]);
      expect(graph.getElementById(method.id).parent().first().id()).toBe(classId);
      for (const [id, position] of before)
        if (id !== classId) expect(graph.getElementById(id).position()).toEqual(position);
    },
  );

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
  it("comes from the theme's tokens, so a larger body type keeps larger nodes apart", () => {
    const large = { ...themes.dark, "body-size": "30px" };
    cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(large) });
    const add = vi.spyOn(cy, "add");
    const node = (id: string): GraphElement => ({
      group: "nodes",
      data: { id, kind: "function" },
      classes: "function",
    });
    updateGraph(cy, [node("a"), node("b")], large);
    const [a, b] = (add.mock.calls[0]?.[0] as GraphElement[]).map((e) => required(e.position));
    // A leaf at 30px body type is 300x120 plus its border, and a margin is kept around it.
    const apart =
      Math.abs(required(a).x - required(b).x) >= 301 + 10 ||
      Math.abs(required(a).y - required(b).y) >= 121 + 10;
    expect(apart).toBe(true);
  });
});
