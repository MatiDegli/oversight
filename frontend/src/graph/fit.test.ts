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

import { graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

cytoscape.use(fcose);

it("fits the camera and sizes the preset with the same padding", () => {
  const cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(themes.dark) });
  try {
    const layout = vi.spyOn(cy, "layout");
    updateGraph(cy, [
      {
        group: "nodes",
        data: { id: "a", kind: "function" },
        classes: "function",
        position: { x: 0, y: 0 },
      },
    ]);
    expect(layout).toHaveBeenCalledWith({ name: "preset", fit: true, padding: 55 });
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
