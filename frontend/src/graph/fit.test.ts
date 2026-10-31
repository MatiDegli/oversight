import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import { expect, it, vi } from "vitest";

// One constant sets the camera's padding in updateGraph and the viewport the preset is sized for
// in tools/layout.ts: change it here and both follow.
vi.mock("./geometry", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./geometry")>()),
  FIT_PADDING: 55,
}));

import { watchViewport } from "../../tools/layout.ts";
import { themes } from "../theme";

import type { GraphElement } from "./elements";
import { graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

cytoscape.use(fcose);

const leaf = (id: string, position?: { x: number; y: number }): GraphElement => ({
  group: "nodes",
  data: { id, kind: "function" },
  classes: "function",
  ...(position ? { position } : {}),
});

it("fits a graph without a preset with the camera's padding, and sizes the preset with the same", () => {
  const cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(themes.dark) });
  try {
    const layout = vi.spyOn(cy, "layout");
    updateGraph(cy, [leaf("a"), leaf("b")]);
    expect(layout).toHaveBeenCalledWith({ name: "fcose", animate: false, fit: true, padding: 55 });
    const themeTokens = themes.dark;
    expect(watchViewport.width).toBe(
      1920 -
        Number.parseFloat(themeTokens["left-rail-width"]) -
        Number.parseFloat(themeTokens["agents-panel-width"]) -
        110,
    );
    expect(watchViewport.height).toBe(
      1080 - Number.parseFloat(themeTokens["top-bar-height"]) - 110,
    );
  } finally {
    cy.destroy();
  }
});

it("falls back to 100% for a preset when the viewport has no size", () => {
  const cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(themes.dark) });
  try {
    const layout = vi.spyOn(cy, "layout");
    cy.zoom(0.3);
    updateGraph(cy, [leaf("a", { x: 0, y: 0 }), leaf("b", { x: 4000, y: 3000 })]);
    expect(layout).toHaveBeenCalledWith({ name: "preset", fit: false });
    expect(cy.zoom()).toBe(1);
  } finally {
    cy.destroy();
  }
});
