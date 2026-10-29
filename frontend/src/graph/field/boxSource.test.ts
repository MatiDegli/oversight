import cytoscape from "cytoscape";
import { expect, it } from "vitest";

import { getActiveTheme } from "../../theme";
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

it("gives an open module's whole frame, where boxes() gives only its header", () => {
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
    const frame = source.frames?.(["module", "missing"]);
    const header = source.boxes(["module"]).get("module");
    expect(frame?.size).toBe(1);
    const whole = frame?.get("module");
    expect((whole?.y2 ?? 0) - (whole?.y1 ?? 0)).toBeGreaterThan(100);
    expect((whole?.y2 ?? 0) - (whole?.y1 ?? 0)).toBeGreaterThan(
      (header?.y2 ?? 0) - (header?.y1 ?? 0),
    );
    expect(whole?.y1).toBe(header?.y1);
    expect(source.edgesAt?.("row")).toEqual([]);
    source.detach();
    expect(source.frames?.(["module"]).size).toBe(0);
    expect(source.edgesAt?.("row")).toEqual([]);
  } finally {
    source.detach();
    cy.destroy();
  }
});

it("masks an open frame's header band, padded, and a folded card by its own box, and leaves the body to the light", () => {
  const cy = cytoscape({
    headless: true,
    styleEnabled: true,
    layout: { name: "preset" },
    elements: [
      { data: { id: "module" } },
      { data: { id: "row", parent: "module" }, position: { x: 100, y: 200 } },
      { data: { id: "folded" }, position: { x: 600, y: 200 } },
    ],
    style: [
      { selector: "node", style: { width: 200, height: 200 } },
      { selector: "node[id = 'module'], node[id = 'folded']", style: { "background-opacity": 1 } },
    ],
  });
  cy.nodes("[id = 'module'], [id = 'folded']").addClass("module");
  const source = createBoxSource();
  try {
    expect(source.headers?.()).toEqual([]);
    source.attach(cy);
    cy.zoom(0.5);
    const theme = getActiveTheme();
    const band = Number.parseFloat(theme["frame-pad-top"]) * 0.5;
    const pad = Number.parseFloat(theme["header-mask-pad"]);
    const whole = source.frames?.(["module", "folded"]);
    const found = source.headers?.() ?? [];
    expect(found).toHaveLength(2);
    const open = whole?.get("module");
    const folded = whole?.get("folded");
    // The open frame hides its header band only: the body below stays lit.
    expect(found).toContainEqual({
      x1: (open?.x1 ?? 0) - pad,
      y1: (open?.y1 ?? 0) - pad,
      x2: (open?.x2 ?? 0) + pad,
      y2: (open?.y1 ?? 0) + band + pad,
    });
    expect((open?.y2 ?? 0) - (open?.y1 ?? 0)).toBeGreaterThan(band + 2 * pad);
    // A folded card is one header: its own box hides the light, with no pad, as a row's does.
    expect(found).toContainEqual({
      x1: folded?.x1 ?? 0,
      y1: folded?.y1 ?? 0,
      x2: folded?.x2 ?? 0,
      y2: folded?.y2 ?? 0,
    });
    source.detach();
    expect(source.headers?.()).toEqual([]);
  } finally {
    source.detach();
    cy.destroy();
  }
});
