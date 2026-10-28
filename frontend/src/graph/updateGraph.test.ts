import cytoscape from "cytoscape";
import { expect, it } from "vitest";
import { themes } from "../theme";
import type { GraphElement } from "./elements";
import { graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

it("clears dimensions and aggregated counts when a folded presentation is removed", () => {
  const cy = cytoscape({ headless: true });
  const node: GraphElement = {
    group: "nodes",
    data: { id: "a", kind: "module", w: 326, h: 70, callCount: 3 },
    classes: "module folded",
    position: { x: 0, y: 0 },
  };
  try {
    updateGraph(cy, [node]);
    updateGraph(cy, [{ ...node, data: { id: "a", kind: "module" }, classes: "module" }]);
    for (const key of ["callCount", "w", "h"])
      expect(cy.getElementById("a").data(key)).toBeUndefined();
  } finally {
    cy.destroy();
  }
});

it("settles routes again when positions change without a topology change", () => {
  const cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(themes.dark) });
  const elements: GraphElement[] = [
    ...["a", "b"].map((id, i): GraphElement => ({
      group: "nodes",
      data: { id, label: id, kind: "module", w: 326, h: 70 },
      classes: "module",
      position: { x: i * 600, y: 0 },
    })),
    {
      group: "edges",
      data: { id: "ab", source: "a", target: "b", kind: "calls" },
      classes: "calls exact",
    },
  ];
  try {
    updateGraph(cy, elements, themes.dark);
    const edge = cy.getElementById("ab");
    const original: unknown = structuredClone(edge.data("route"));
    cy.getElementById("b").position({ x: 600, y: 200 });
    updateGraph(cy, elements, themes.dark);
    expect(edge.data("route")).not.toEqual(original);
  } finally {
    cy.destroy();
  }
});
