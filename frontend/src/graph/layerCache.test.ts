import cytoscape from "cytoscape";
import { expect, it, vi } from "vitest";
import { themes } from "../theme";
import * as layers from "./layers";
import { graphStyles } from "./styles";

it("generates one SVG per presentation key across the nine layer callbacks", () => {
  const generated = vi.spyOn(layers, "marksLayer");
  const marks = { word: "read", slot: "A", badges: [{ slot: "A", filled: false }] };
  const cy = cytoscape({
    headless: true,
    styleEnabled: true,
    style: graphStyles(themes.dark),
    elements: [
      {
        group: "nodes",
        data: { id: "f", label: "function", w: 282, h: 46, marks },
        classes: "function marked",
      },
    ],
  });
  try {
    const node = cy.getElementById("f");
    const original: unknown = node.style("background-image");
    expect(generated).toHaveBeenCalledTimes(1);
    for (let i = 0; i < 20; i++) {
      node.data("unrelated", i);
      expect(node.style("background-image")).toBe(original);
      node.style("background-width");
      node.style("background-height");
    }
    expect(generated).toHaveBeenCalledTimes(1);
    node.data("marks", { ...marks, word: "changed", badges: [{ slot: "A", filled: true }] });
    expect(node.style("background-image")).not.toBe(original);
    expect(generated).toHaveBeenCalledTimes(2);
  } finally {
    cy.destroy();
    generated.mockRestore();
  }
});
