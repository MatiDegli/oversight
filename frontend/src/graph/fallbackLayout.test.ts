import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import { expect, it, vi } from "vitest";

import { themes } from "../theme";
import type { GraphElement } from "./elements";
import { graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

cytoscape.use(fcose);

it("packs final module frames without intersections when positions are absent", () => {
  const elements: GraphElement[] = Array.from({ length: 34 }, (_, i) => ({
    group: "nodes",
    data: { id: `m${String(i).padStart(2, "0")}`, kind: "module", w: 326, h: 70 },
    classes: "module folded",
  }));
  elements.push(
    { group: "nodes", data: { id: "class", parent: "m00", kind: "class" }, classes: "class" },
    ...Array.from({ length: 5 }, (_, i): GraphElement => ({
      group: "nodes",
      data: { id: `f${String(i)}`, parent: "class", kind: "method", label: "method" },
      classes: "method",
    })),
  );
  const cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(themes.dark) });
  try {
    vi.spyOn(cy, "width").mockReturnValue(1464);
    vi.spyOn(cy, "height").mockReturnValue(1028);
    updateGraph(cy, elements);
    const boxes = cy.nodes(":orphan").map((node) => node.boundingBox({ includeLabels: true }));
    for (let i = 0; i < boxes.length; i++)
      for (const b of boxes.slice(i + 1)) {
        const a = boxes[i];
        expect(a && a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1).toBe(false);
      }
    expect(cy.getElementById("m00").data("minH")).toBeGreaterThan(70);
    const placed = cy.nodes(":childless").map((node) => ({ ...node.position() }));
    updateGraph(cy, elements);
    expect(cy.nodes(":childless").map((node) => ({ ...node.position() }))).toEqual(placed);
  } finally {
    cy.destroy();
  }
});

it("keeps supplied preset positions and uses the preset layout without fitting", () => {
  const cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(themes.dark) });
  const elements: GraphElement[] = ["a", "b"].map((id, i) => ({
    group: "nodes",
    data: { id, kind: "module", w: 326, h: 70 },
    classes: "module folded",
    position: { x: i * 600, y: i * 1000 },
  }));
  try {
    const layout = vi.spyOn(cy, "layout");
    const fit = vi.spyOn(cy, "fit");
    updateGraph(cy, elements);
    expect(layout).toHaveBeenCalledExactlyOnceWith({ name: "preset", fit: false });
    expect(fit).not.toHaveBeenCalled();
    for (const e of elements) expect(cy.getElementById(e.data.id).position()).toEqual(e.position);
  } finally {
    cy.destroy();
  }
});
