import cytoscape from "cytoscape";
import { expect, it, vi } from "vitest";
import type { GraphElement } from "./elements";
import { prepareGraphLayout } from "./mapController";
import { updateGraph } from "./updateGraph";

it("loads the fallback solver only for unplaced nodes and retries only living graphs", async () => {
  const first = cytoscape({ headless: true });
  const second = cytoscape({ headless: true });
  const node: GraphElement = {
    group: "nodes",
    data: { id: "lib", kind: "module" },
    classes: "module",
  };
  try {
    const retry = vi.fn();
    expect(prepareGraphLayout(first, [], retry)).toBe(true);
    expect(prepareGraphLayout(first, [{ ...node, position: { x: 10, y: 20 } }], retry)).toBe(true);
    expect(retry).not.toHaveBeenCalled();
    expect(prepareGraphLayout(first, [node], retry)).toBe(false);
    const ready = new Promise<void>((resolve) => {
      expect(
        prepareGraphLayout(second, [node], () => {
          resolve();
        }),
      ).toBe(false);
    });
    first.destroy();
    await ready;
    expect(retry).not.toHaveBeenCalled();
    expect(prepareGraphLayout(second, [node], retry)).toBe(true);
    second.add({ data: { id: "lib" } });
    const options = { name: "fcose", randomize: false, animate: false };
    expect(() => second.layout(options).run()).not.toThrow();
    expect(second.getElementById("lib").position()).toEqual({ x: 0, y: 0 });
  } finally {
    first.destroy();
    second.destroy();
  }
});

it("draws nodes after a rejected solver load and retries on the next sync", async () => {
  vi.resetModules();
  vi.doMock("cytoscape-fcose", () => {
    throw new Error("Offline solver chunk");
  });
  const { prepareGraphLayout: prepare } = await import("./mapController");
  const cy = cytoscape({ headless: true, styleEnabled: true });
  const nodes: GraphElement[] = [0, 1].map((i) => ({
    group: "nodes",
    data: { id: `module-${String(i)}`, kind: "module", title: `module-${String(i)}` },
    classes: "module",
  }));
  try {
    const draw = vi.fn((fallback?: GraphElement[]) => {
      if (fallback) updateGraph(cy, fallback);
    });
    expect(prepare(cy, nodes, draw)).toBe(false);
    await vi.waitFor(() => {
      expect(draw).toHaveBeenCalledOnce();
    });
    expect(cy.nodes()).toHaveLength(2);
    expect(new Set(cy.nodes().map((n) => JSON.stringify(n.position()))).size).toBe(2);
    expect(nodes.every((node) => node.position === undefined)).toBe(true);
    vi.doMock("cytoscape-fcose", () => vi.importActual("cytoscape-fcose"));
    const retry = vi.fn();
    expect(prepare(cy, nodes, retry)).toBe(false);
    await vi.waitFor(() => {
      expect(retry).toHaveBeenCalledOnce();
    });
    expect(prepare(cy, nodes, retry)).toBe(true);
    const layout = { name: "fcose", animate: false };
    expect(() => cy.layout(layout).run()).not.toThrow();
  } finally {
    cy.destroy();
    vi.doUnmock("cytoscape-fcose");
  }
});
