import { readFileSync } from "node:fs";

import type { GraphSnapshot } from "@contracts";
import cytoscape from "cytoscape";
import type { Core } from "cytoscape";
import { afterEach, describe, expect, it } from "vitest";

import { createInitialState } from "../state";
import { themes } from "../theme";

import { graphElements } from "./elements";
import { frameExtras } from "./frames";
import { graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

const root = new URL("../../../", import.meta.url);
const snapshot = JSON.parse(
  readFileSync(new URL("fixtures/graph/demo-repo.json", root), "utf8"),
) as GraphSnapshot;
const preset = JSON.parse(
  readFileSync(new URL("fixtures/layouts/demo-repo.json", root), "utf8"),
) as GraphSnapshot["positions"];
const theme = themes.dark;

let cy: Core | undefined;
afterEach(() => {
  cy?.destroy();
  cy = undefined;
});

function demo() {
  cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(theme) });
  updateGraph(
    cy,
    graphElements({ ...createInitialState(), graph: { ...snapshot, positions: preset } }),
  );
  return cy;
}

/** A node's outer box, as the sketch measures it: its drawn edge to its drawn edge. */
const outer = (graph: Core, id: string): [number, number] => {
  const node = graph.getElementById(id);
  return [node.outerWidth(), node.outerHeight()];
};

describe("frameExtras", () => {
  it("is each frame's larger sides less its smaller, from the sketch's paddings", () => {
    expect(frameExtras(theme)).toEqual({
      module: { left: 27 - 17, top: 54 - 17 },
      class: { left: 0, top: 44 - 12 },
    });
  });
});

describe("frames at the sketch's size", () => {
  it("gives a row the sketch's 282x46, and a row in a class the class's side paddings less", () => {
    const graph = demo();
    expect(outer(graph, "shop.cart:Cart.add")).toEqual([258, 46]);
    expect(outer(graph, "shop.pricing:compute_price")).toEqual([282, 46]);
  });

  it("sizes a frame around its rows: 27 left, 54 above, 17 right and 17 below", () => {
    const graph = demo();
    const rows = graph.getElementById("shop.pricing").children();
    expect(rows).toHaveLength(6);
    const [width, height] = outer(graph, "shop.pricing");
    // Cytoscape counts the rows' strokes in a frame's bounds: within 2px of the sketch's size.
    expect(Math.abs(width - (27 + 282 + 17))).toBeLessThanOrEqual(2);
    // Six rows 11px apart, between the header band and the bottom padding.
    expect(Math.abs(height - (54 + 6 * 46 + 5 * 11 + 17))).toBeLessThanOrEqual(2);
  });

  it("sizes a class frame in its module: as wide as a module's row, 44 above and 12 below", () => {
    const graph = demo();
    const methods = graph.getElementById("shop.cart:Cart").children();
    expect(methods.length).toBeGreaterThan(1);
    const [width, height] = outer(graph, "shop.cart:Cart");
    expect(Math.abs(width - 282)).toBeLessThanOrEqual(2);
    expect(
      Math.abs(height - (44 + methods.length * 46 + (methods.length - 1) * 11 + 12)),
    ).toBeLessThanOrEqual(2);
  });

  it("draws a module or class with nothing in it as one 70px header row", () => {
    const graph = demo();
    expect(outer(graph, "shop")).toEqual([326, 70]);
    expect(outer(graph, "shop.models:OrderLine")).toEqual([282, 70]);
  });

  it("keeps every row inside its frame, below the header band, and moves no row", () => {
    const graph = demo();
    for (const row of graph.nodes(".function, .method").toArray()) {
      const frame = row.parent().first();
      const inner = row.boundingBox({ includeLabels: false });
      const box = frame.boundingBox({ includeLabels: false });
      expect(inner.y1, row.id()).toBeGreaterThanOrEqual(
        box.y1 + (frame.hasClass("class") ? 44 : 54) - 2,
      );
      expect(inner.x1, row.id()).toBeGreaterThan(box.x1);
      expect(inner.x2, row.id()).toBeLessThan(box.x2);
      expect(row.position(), row.id()).toEqual(preset[row.id()]);
    }
  });
});
