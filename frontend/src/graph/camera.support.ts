import type { Core } from "cytoscape";
import cytoscape from "cytoscape";
import { afterEach, vi } from "vitest";

import { getActiveTheme } from "../theme";
import { ZOOM_RANGE } from "./camera";

const theme = getActiveTheme();
const graphs: Core[] = [];
// Importing this module registers cleanup for every test, even when only a constant is used.
afterEach(() => {
  for (const cy of graphs.splice(0)) cy.destroy();
  vi.unstubAllGlobals();
});

/**
 * A wide row of nodes (the call chain a -> b -> c at the far right, `lone` apart from it) in a
 * viewport that has just been narrowed to `width` by the left panel.
 */
function narrowedGraph(width: number): Core {
  const cy = cytoscape({
    headless: true,
    styleEnabled: true,
    layout: { name: "preset" },
    elements: [
      ...["a", "b", "c", "d", "lone"].map((id, index) => ({
        data: { id },
        position: { x: index * 400, y: 100 + index * 10 },
      })),
      { data: { id: "ab", source: "a", target: "b" } },
      { data: { id: "bc", source: "b", target: "c" } },
      { data: { id: "cd", source: "c", target: "d" } },
    ],
  });
  graphs.push(cy);
  // Headless Cytoscape has no container to measure.
  vi.spyOn(cy, "width").mockReturnValue(width);
  vi.spyOn(cy, "height").mockReturnValue(600);
  vi.spyOn(cy, "resize").mockReturnValue(cy);
  // The wide picture the panel's opening found: the whole row fitted at a wider viewport.
  cy.viewport({ zoom: 1, pan: { x: 20, y: 40 } });
  return cy;
}

const positions = (cy: Core) =>
  Object.fromEntries(cy.nodes().map((node) => [node.id(), node.position()]));

function inView(cy: Core, ids: string[]) {
  return ids.every((id) => {
    const { x, y } = cy.getElementById(id).renderedPosition();
    return x >= 0 && x <= cy.width() && y >= 0 && y <= cy.height();
  });
}

const limits = { padding: 40, maxZoom: 4, minZoom: 12 / 15 };
const box = (width: number, height: number) => ({
  id: "changed",
  x1: 100,
  y1: 50,
  x2: 100 + width,
  y2: 50 + height,
});
const size = { width: 600, height: 400 };

/** A graph far wider than the view, built as GraphView builds its instance: with the zoom range. */
function wideGraph(spread: number): Core {
  const cy = cytoscape({
    headless: true,
    styleEnabled: true,
    layout: { name: "preset" },
    minZoom: ZOOM_RANGE.min,
    maxZoom: ZOOM_RANGE.max,
    elements: [
      { data: { id: "west" }, position: { x: 0, y: 100 } },
      { data: { id: "east" }, position: { x: spread, y: 200 } },
    ],
  });
  graphs.push(cy);
  vi.spyOn(cy, "width").mockReturnValue(1000);
  vi.spyOn(cy, "height").mockReturnValue(600);
  vi.spyOn(cy, "resize").mockReturnValue(cy);
  cy.viewport({ zoom: 1, pan: { x: 20, y: 40 } });
  return cy;
}

export { box, graphs, inView, limits, narrowedGraph, positions, size, theme, wideGraph };
