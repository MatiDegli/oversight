import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import { afterEach, expect, it, vi } from "vitest";

const order = vi.hoisted(() => [] as string[]);
vi.mock("./frames", () => ({ applyFrames: () => order.push("frames") }));
vi.mock("./routes", () => ({ applyRoutes: () => order.push("routes") }));
vi.mock("./camera", () => ({ startCamera: () => order.push("camera") }));
vi.mock("./fallbackLayout", () => ({ packFallback: () => order.push("pack") }));

import { updateGraph } from "./updateGraph";

cytoscape.use(fcose);
afterEach(() => {
  order.length = 0;
  vi.restoreAllMocks();
});

it.each([true, false])(
  "settles after the initial layout and before the camera (preset=%s)",
  (preset) => {
    const cy = cytoscape({ headless: true });
    const elements = [
      {
        group: "nodes" as const,
        data: { id: "module", kind: "module" as const },
        classes: "module",
        ...(preset ? { position: { x: 100, y: 200 } } : {}),
      },
    ];
    const layout = cy.layout.bind(cy);
    vi.spyOn(cy, "layout").mockImplementation((options) => {
      const result = layout(options);
      const run = result.run.bind(result);
      vi.spyOn(result, "run").mockImplementation(() => {
        run();
        order.push(options.name);
        return result;
      });
      return result;
    });
    try {
      updateGraph(cy, elements);
      expect(order).toEqual(
        preset ? ["preset", "frames", "routes", "camera"] : ["fcose", "pack", "frames", "routes"],
      );
      order.length = 0;
      updateGraph(cy, elements);
      expect(order).toEqual(["frames", "routes"]);
    } finally {
      cy.destroy();
    }
  },
);
