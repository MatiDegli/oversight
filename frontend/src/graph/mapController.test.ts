import cytoscape from "cytoscape";
import { expect, it, vi } from "vitest";
import type { GraphElement } from "./elements";
import { prepareGraphLayout } from "./mapController";

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
      expect(prepareGraphLayout(second, [node], resolve)).toBe(false);
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
