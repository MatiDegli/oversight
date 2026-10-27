import { readFileSync } from "node:fs";

import type { GraphSnapshot } from "@contracts";
import cytoscape from "cytoscape";
import { expect, it, vi } from "vitest";

import { createInitialState } from "../state";
import { themes } from "../theme";

import { startCamera } from "./camera";
import { graphElements } from "./elements";
import { foldController } from "./foldController";
import { graphStyles } from "./styles";

const graph = JSON.parse(
  readFileSync(new URL("../../../fixtures/graph/demo-repo.json", import.meta.url), "utf8"),
) as GraphSnapshot;
graph.positions = JSON.parse(
  readFileSync(new URL("../../../fixtures/layouts/demo-repo.json", import.meta.url), "utf8"),
) as GraphSnapshot["positions"];

it("starts the demo preset with every frame fully inside or outside all four visible edges", () => {
  const cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(themes.dark) });
  try {
    vi.spyOn(cy, "width").mockReturnValue(1464);
    vi.spyOn(cy, "height").mockReturnValue(1028);
    const state = { ...createInitialState(), graph };
    foldController(cy).render(state, graphElements(state));
    const positions = cy.nodes(":childless").map((node) => ({ ...node.position() }));
    // Watch heading and footer excluded, in graph-local screen pixels at the demo viewport.
    const area = { x1: 24, y1: 60, x2: 1440, y2: 892 };
    startCamera(cy, area);
    expect(cy.zoom()).toBe(1);
    let inside = 0;
    let outside = 0;
    for (const frame of cy.nodes(".module, .class")) {
      const b = frame.renderedBoundingBox({ includeLabels: false, includeOverlays: false });
      const contained = b.x1 >= area.x1 && b.y1 >= area.y1 && b.x2 <= area.x2 && b.y2 <= area.y2;
      const excluded = b.x2 <= 0 || b.y2 <= 0 || b.x1 >= cy.width() || b.y1 >= cy.height();
      expect(contained || excluded, `${frame.id()}: ${JSON.stringify(b)}`).toBe(true);
      if (contained) inside++;
      if (excluded) outside++;
    }
    expect(inside).toBeGreaterThan(0);
    expect(outside).toBeGreaterThan(0);
    const hub = cy.getElementById("shop.pricing").renderedBoundingBox({ includeLabels: false });
    expect(hub.x1).toBeGreaterThanOrEqual(area.x1);
    expect(hub.y1).toBeGreaterThanOrEqual(area.y1);
    expect(hub.x2).toBeLessThanOrEqual(area.x2);
    expect(hub.y2).toBeLessThanOrEqual(area.y2);
    expect(cy.nodes(":childless").map((node) => ({ ...node.position() }))).toEqual(positions);
  } finally {
    cy.destroy();
    vi.restoreAllMocks();
  }
});
