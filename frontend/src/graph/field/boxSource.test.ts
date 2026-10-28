import cytoscape from "cytoscape";
import { expect, it } from "vitest";

import { createBoxSource } from "./boxSource";

it("keeps a hidden method's light on its class row, then follows its expanded box and camera", () => {
  const cy = cytoscape({
    headless: true,
    styleEnabled: true,
    layout: { name: "preset" },
    elements: [{ data: { id: "Cart" }, position: { x: 100, y: 200 } }],
  });
  const source = createBoxSource();
  try {
    cy.scratch("foldAliases", new Map([["Cart.add", "Cart"]]));
    source.attach(cy);
    const box = (id: string) => {
      const { x1, y1, x2, y2 } = cy.getElementById(id).renderedBoundingBox({
        includeLabels: false,
        includeOverlays: false,
        includeEdges: false,
      });
      return { x1, y1, x2, y2 };
    };
    expect(source.boxes(["Cart.add"]).get("Cart.add")).toEqual(box("Cart"));
    cy.add({ data: { id: "Cart.add" }, position: { x: 100, y: 260 } });
    cy.scratch("foldAliases", new Map());
    cy.zoom(0.8);
    cy.pan({ x: 30, y: 40 });
    expect(source.boxes(["Cart.add"]).get("Cart.add")).toEqual(box("Cart.add"));
    expect(source.boxes(["missing"]).size).toBe(0);
    source.detach();
    expect(source.boxes(["Cart.add"]).size).toBe(0);
  } finally {
    source.detach();
    cy.destroy();
  }
});

it("uses the folded card or the open header, never an open module's whole frame, and exposes zoom", () => {
  const cy = cytoscape({
    headless: true,
    styleEnabled: true,
    layout: { name: "preset" },
    elements: [
      { data: { id: "module" } },
      { data: { id: "row", parent: "module" }, position: { x: 100, y: 200 } },
    ],
    style: [{ selector: "node", style: { width: 200, height: 200 } }],
  });
  const source = createBoxSource();
  try {
    source.attach(cy);
    cy.zoom(0.5);
    const box = source.boxes(["module"]).get("module");
    expect(source.zoom?.()).toBe(0.5);
    expect((box?.y2 ?? 0) - (box?.y1 ?? 0)).toBe(35);
    expect(source.boxes(["row"]).get("row")?.y2).toBeGreaterThan(box?.y2 ?? 0);
    expect(source.route?.("row", "module")).toBeNull();
  } finally {
    source.detach();
    cy.destroy();
  }
});
