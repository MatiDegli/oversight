import cytoscape from "cytoscape";
import { expect, it, vi } from "vitest";

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

it("gives a hidden edge no route, so its marker travels straight", async () => {
  const { MarkerMotion } = await import("./marker");
  const cy = cytoscape({
    headless: true,
    styleEnabled: true,
    layout: { name: "preset", fit: false },
    elements: [
      { data: { id: "a" }, position: { x: 0, y: 0 } },
      { data: { id: "b" }, position: { x: 100, y: 100 } },
      { data: { id: "hidden", source: "a", target: "b" } },
    ],
    style: [{ selector: "edge", style: { display: "none" } }],
  });
  const source = createBoxSource();
  try {
    const edge = cy.getElementById("hidden");
    // A hidden edge can retain endpoints from its last rendered, bent route.
    vi.spyOn(edge, "renderedSourceEndpoint").mockReturnValue({ x: 0, y: 0 });
    vi.spyOn(edge, "renderedTargetEndpoint").mockReturnValue({ x: 100, y: 100 });
    vi.spyOn(edge, "renderedSegmentPoints").mockReturnValue([{ x: 0, y: 100 }]);
    source.attach(cy);
    expect(source.route?.("a", "b")).toBeNull();
    const motion = new MarkerMotion();
    const light = {
      id: "a",
      marker: "agent-A",
      source: "agent-a",
      weight: 1,
      pulses: true,
      steady: true,
    } as const;
    motion.set([light], source, 0, "240ms", "linear", false);
    const next = { ...light, id: "b" };
    motion.set([next], source, 300, "240ms", "linear", false);
    const box = motion.boxes([next], source, 420).get("agent-A\u0000b");
    const origin = source.boxes(["a"]).get("a");
    expect((box?.x1 ?? 0) - (origin?.x1 ?? 0)).toBeCloseTo(50);
    expect((box?.y1 ?? 0) - (origin?.y1 ?? 0)).toBeCloseTo(50);
  } finally {
    source.detach();
    cy.destroy();
  }
});
