import cytoscape from "cytoscape";
import { expect, it, vi } from "vitest";

import { themes } from "../theme";

import { applyRoutes } from "./routes";
import type { Route } from "./routes";

it("skips equal route writes but repairs a route whose current data changed", () => {
  const cy = cytoscape({
    headless: true,
    styleEnabled: true,
    style: [{ selector: "node", style: { width: 282, height: 46 } }],
    elements: [
      { data: { id: "a" }, position: { x: 0, y: 0 } },
      { data: { id: "b" }, position: { x: 600, y: 120 } },
      { data: { id: "call", source: "a", target: "b" }, classes: "calls" },
    ],
    layout: { name: "preset" },
  });
  try {
    applyRoutes(cy, themes.dark);
    const call = cy.getElementById("call");
    const stored = call.data("route") as Route;
    expect(stored).toBeDefined();
    const expected = structuredClone(stored);
    const writes = vi.fn();
    cy.on("data", "edge", writes);
    applyRoutes(cy, themes.dark);
    expect(writes).not.toHaveBeenCalled();
    expect(call.data("route")).toBe(stored);
    // Compare actual data each time; a cached reference would miss this stale value.
    stored.s[0] += 1;
    applyRoutes(cy, themes.dark);
    expect(writes).toHaveBeenCalledTimes(1);
    expect(call.data("route")).not.toBe(stored);
    expect(call.data("route")).toEqual(expected);
  } finally {
    cy.destroy();
  }
});
