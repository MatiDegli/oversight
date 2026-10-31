import type { Core } from "cytoscape";
import cytoscape from "cytoscape";
import { afterEach, describe, expect, it, vi } from "vitest";

import { stateStore } from "../state";

import { loadGraphEvents } from "./devLog";
import { graphElements } from "./elements";
import { graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

import { a, event, initial, log, node, nodes, render, theme, updated } from "./graph.support";

let cy: Core | undefined;
afterEach(() => {
  cy?.destroy();
});

describe("graph updates and log entry point", () => {
  it("starts empty and feeds inline events through the shared store without a transport", () => {
    expect(graphElements(stateStore.getState().state)).toEqual([]);
    loadGraphEvents(log);
    expect(stateStore.getState().state).toEqual(initial());
    expect(graphElements(stateStore.getState().state)).toHaveLength(7);
    loadGraphEvents([
      event(5, "agent.focus", {
        agent_id: a.agent_id,
        mode: "edit",
        node_ids: ["billing:fee"],
        ranges: [],
      }),
    ]);
    expect(
      graphElements(stateStore.getState().state).find((e) => e.data.id === "billing:fee")?.classes,
    ).toContain("focus-A-edit");
  });

  it("uses preset positions, then adds a graph.delta node without moving existing leaves or viewport", () => {
    loadGraphEvents(log);
    const graph = render(stateStore.getState().state);
    expect(graph.getElementById("billing:fee").position()).toEqual({ x: 400, y: 100 });
    graph.getElementById("billing:fee").position({ x: 600, y: 120 }); // Human drag beats stale preset.
    expect(stateStore.getState().state.graph?.positions["billing:fee"]).toEqual({ x: 400, y: 100 });
    const positions = graph
      .nodes(":childless")
      .map((n) => ({ id: n.id(), position: { ...n.position() } }));
    graph.zoom(1.7);
    graph.pan({ x: 20, y: 40 });
    const layout = vi.spyOn(graph, "layout");
    const batch = vi.spyOn(graph, "batch");
    loadGraphEvents([
      event(5, "graph.delta", {
        from_version: "v1",
        to_version: "v2",
        commit: "next",
        cause: "rescan",
        nodes_added: [node("billing:tax", "function", "billing")],
        nodes_changed: [],
        nodes_removed: [],
        edges_added: [],
        edges_changed: [],
        edges_removed: [],
        parse_errors: [],
      }),
    ]);
    updateGraph(graph, graphElements(stateStore.getState().state));
    // One batch for the elements, however many; one more for each level of frames (the class, then
    // the module) and one for the routes.
    expect(batch).toHaveBeenCalledTimes(4);
    expect(layout).not.toHaveBeenCalled();
    for (const { id, position } of positions)
      expect(graph.getElementById(id).position()).toEqual(position);
    const added = graph.getElementById("billing:tax");
    expect(added.parent().first().id()).toBe("billing");
    expect(Number.isFinite(added.position().x)).toBe(true);
    expect(graph.zoom()).toBe(1.7);
    expect(graph.pan()).toEqual({ x: 20, y: 40 });
  });

  // 1260 puts a's frame 30px from b's, at the width of a row: close enough that b cannot grow
  // toward it.
  it.each([0, 1260])(
    "places additions at b's box without growing b across another module at %s",
    (aX) => {
      const state = initial();
      if (!state.graph) throw new Error("Missing test graph");
      state.graph = {
        ...state.graph,
        nodes: [
          node("a", "module"),
          node("a:f", "function", "a"),
          node("b", "module"),
          node("b:f", "function", "b"),
        ],
        edges: [],
        positions: { "a:f": { x: aX, y: 300 }, "b:f": { x: 900, y: 300 } },
      };
      const graph = render(state);
      const bounds = (id: string) => graph.getElementById(id).boundingBox({ includeLabels: false });
      const aBox = bounds("a");
      const bBox = bounds("b");
      const before = graph.getElementById("b:f").position();
      state.graph.nodes.push(node("b:new", "function", "b"));
      updateGraph(graph, graphElements(state));
      const added = graph.getElementById("b:new");
      const point = added.position();
      const dx = Math.max(bBox.x1 - point.x, 0, point.x - bBox.x2);
      const dy = Math.max(bBox.y1 - point.y, 0, point.y - bBox.y2);
      expect(Math.hypot(dx, dy)).toBeLessThanOrEqual(140);
      const grown = bounds("b");
      expect(
        grown.x1 >= aBox.x2 || grown.x2 <= aBox.x1 || grown.y1 >= aBox.y2 || grown.y2 <= aBox.y1,
      ).toBe(true);
      expect(graph.getElementById("b:f").position()).toEqual(before);
      expect(Math.abs(point.x - before.x) >= 150 || Math.abs(point.y - before.y) >= 60).toBe(true);
      expect(bounds("a")).toEqual(aBox);
    },
  );

  it("avoids sibling compound boxes including labels when placing a new node", () => {
    const state = initial();
    if (!state.graph) throw new Error("Missing graph");
    state.graph = {
      ...state.graph,
      nodes: [
        node("m", "module"),
        node("c", "class", "m"),
        node("left", "method", "c"),
        node("right", "method", "c"),
      ],
      edges: [],
      positions: { left: { x: 0, y: 0 }, right: { x: 500, y: 0 } },
    };
    const graph = render(state);
    const sibling = graph.getElementById("c");
    const measure = vi.spyOn(sibling, "boundingBox");
    const before = sibling.boundingBox({ includeLabels: true, includeOverlays: false });
    measure.mockClear();
    state.graph.nodes.push(node("new", "function", "m"));
    updateGraph(graph, graphElements(state));
    const added = graph
      .getElementById("new")
      .boundingBox({ includeLabels: true, includeOverlays: false });
    expect(
      added.x1 >= before.x2 ||
        added.x2 <= before.x1 ||
        added.y1 >= before.y2 ||
        added.y2 <= before.y1,
    ).toBe(true);
    expect(measure).toHaveBeenCalledWith({ includeLabels: true, includeOverlays: false });
    expect(sibling.boundingBox({ includeLabels: true, includeOverlays: false })).toEqual(before);
  });

  it("runs fcose once when there is no preset and retains those positions on updates", () => {
    const state = initial();
    if (!state.graph) throw new Error("Missing test graph");
    state.graph = { ...state.graph, positions: {} };
    cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(theme) });
    const layout = vi.spyOn(cy, "layout");
    updateGraph(cy, graphElements(state));
    expect(layout).toHaveBeenCalledWith(expect.objectContaining({ name: "fcose", animate: false }));
    const positions = cy
      .nodes(":childless")
      .map((n) => ({ id: n.id(), position: { ...n.position() } }));
    updateGraph(cy, graphElements(updated(state)));
    expect(layout).toHaveBeenCalledTimes(1);
    for (const { id, position } of positions)
      expect(cy.getElementById(id).position()).toEqual(position);
  });

  it("reparents survivors before deleting their old compound parent and updates edge endpoints", () => {
    const state = initial();
    const graph = render(state);
    graph.getElementById("billing:Bill.total").position({ x: 225, y: 200 });
    if (!state.graph) throw new Error("Missing test graph");
    state.graph = {
      ...state.graph,
      nodes: nodes
        .filter((n) => n.kind !== "class")
        .map((n) => (n.kind === "method" ? { ...n, parent_id: "billing" } : n)),
      edges: state.graph.edges.map((e) => ({ ...e, dst: "billing" })),
    };
    updateGraph(graph, graphElements(state));
    expect(graph.getElementById("billing:Bill").empty()).toBe(true);
    expect(graph.getElementById("billing:Bill.total").parent().first().id()).toBe("billing");
    expect(graph.getElementById("billing:Bill.total").position()).toEqual({ x: 225, y: 200 });
    expect(graph.getElementById("edge-calls").target().id()).toBe("billing");
  });
});
