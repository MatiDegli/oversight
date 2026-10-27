import cytoscape from "cytoscape";
import { expect, it } from "vitest";

import { exposeProbe } from "./probe";
import type { GraphProbeHost } from "./probe";

it("returns detached snapshots: changing a probe result cannot move Cytoscape or later readings", () => {
  const cy = cytoscape({
    headless: true,
    styleEnabled: true,
    layout: { name: "preset" },
    elements: [{ data: { id: "a" }, position: { x: 10, y: 20 } }],
  });
  const host = {} as GraphProbeHost;
  const dispose = exposeProbe(host, cy);
  try {
    const probe = host.oversightGraph;
    if (!probe) throw new Error("Missing probe");
    const positions = probe.positions();
    const original = structuredClone(positions);
    const point = positions.a;
    if (!point) throw new Error("Missing position");
    point.x = -999;
    expect(cy.getElementById("a").position()).toEqual(original.a);
    expect(probe.positions()).toEqual(original);
    cy.getElementById("a").position({ x: 30, y: 40 });
    expect(positions.a).toEqual({ x: -999, y: 20 });
    const pan = probe.pan();
    pan.x = -999;
    expect(cy.pan().x).not.toBe(-999);
    const centers = probe.nodeCenters();
    const bounds = probe.nodeBounds();
    const metrics = probe.metrics();
    if (centers[0]) centers[0].x = -999;
    if (bounds[0]) bounds[0].x1 = -999;
    metrics.viewport.width = -999;
    expect(probe.nodeCenters()[0]?.x).not.toBe(-999);
    expect(probe.nodeBounds()[0]?.x1).not.toBe(-999);
    expect(probe.metrics().viewport.width).not.toBe(-999);
  } finally {
    dispose();
    expect(host.oversightGraph).toBeUndefined();
    cy.destroy();
  }
});
