import cytoscape from "cytoscape";
import type { Core } from "cytoscape";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getActiveTheme } from "../theme";
import { frameBox, frameCamera, prefersReducedMotion } from "./camera";

const theme = getActiveTheme();
const graphs: Core[] = [];
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
  cy.viewport({ zoom: 0.5, pan: { x: 20, y: 40 } });
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

const limits = { padding: 40, minZoom: 0.1, maxZoom: 4 };
const box = (width: number, height: number) => ({
  x1: 100,
  y1: 50,
  x2: 100 + width,
  y2: 50 + height,
});
const size = { width: 600, height: 400 };

describe("frameBox", () => {
  it("keeps the zoom it is given and centers the box when the box fits", () => {
    const camera = frameBox(box(100, 50), size, 1, limits);
    expect(camera?.zoom).toBe(1);
    // The box's center, (150, 75), lands on the viewport's center, (300, 200).
    expect(camera?.pan).toEqual({ x: 150, y: 125 });
  });

  it("zooms out only as far as the box needs to fit inside the padding", () => {
    const wide = frameBox(box(2000, 50), size, 1, limits);
    expect(wide?.zoom).toBeCloseTo((600 - 80) / 2000);
    const tall = frameBox(box(100, 1600), size, 1, limits);
    expect(tall?.zoom).toBeCloseTo((400 - 80) / 1600);
    // Exactly the padding is left at the box's long sides.
    const left = 100 * (wide?.zoom ?? 0) + (wide?.pan.x ?? 0);
    expect(left).toBeCloseTo(40);
  });

  it("never zooms in past the zoom it is given, and respects the zoom limits", () => {
    expect(frameBox(box(10, 10), size, 0.5, limits)?.zoom).toBe(0.5);
    expect(frameBox(box(10, 10), size, Number.POSITIVE_INFINITY, limits)?.zoom).toBe(4);
    expect(frameBox(box(100_000, 10), size, 1, limits)?.zoom).toBe(0.1);
    expect(frameBox(box(10, 10), size, 0.01, limits)?.zoom).toBe(0.1);
  });

  it("answers null when the viewport has no room inside the padding", () => {
    expect(frameBox(box(10, 10), { width: 80, height: 400 }, 1, limits)).toBeNull();
    expect(frameBox(box(10, 10), { width: 600, height: 60 }, 1, limits)).toBeNull();
  });
});

describe("frameCamera", () => {
  it("opening keeps the zoom the watch had and centers the lit nodes without moving a node", () => {
    const cy = narrowedGraph(600);
    const before = positions(cy);
    // Without a reframe the camera would stay put and these would sit past the 600 px edge.
    expect(inView(cy, ["c", "d"])).toBe(false);
    frameCamera(cy, ["c", "d"], true);
    expect(cy.zoom()).toBe(0.5);
    expect(inView(cy, ["c", "d"])).toBe(true);
    const lit = cy.collection().union(cy.getElementById("c")).union(cy.getElementById("d"));
    const { x1, x2 } = lit.renderedBoundingBox({ includeLabels: true });
    expect((x1 + x2) / 2).toBeCloseTo(300);
    expect(positions(cy)).toEqual(before);
  });

  it("lets the context around the lit nodes run past the edges", () => {
    const cy = narrowedGraph(600);
    frameCamera(cy, ["c"], true);
    expect(cy.zoom()).toBe(0.5);
    expect(inView(cy, ["c"])).toBe(true);
    // The neighbours are not framed: the one at the far right is cut off, and nothing zoomed out.
    expect(inView(cy, ["d", "lone"])).toBe(false);
  });

  it("zooms out only when the lit nodes do not fit at the zoom it had", () => {
    const cy = narrowedGraph(200);
    frameCamera(cy, ["c", "d"], true);
    expect(cy.zoom()).toBeLessThan(0.5);
    const { x1, x2 } = cy
      .collection()
      .union(cy.getElementById("c"))
      .union(cy.getElementById("d"))
      .renderedBoundingBox({ includeLabels: true });
    expect(x1).toBeGreaterThanOrEqual(40 - 0.5);
    expect(x2).toBeLessThanOrEqual(200 - 40 + 0.5);
  });

  it("closing, or opening with nothing to frame, returns to the fit of the whole graph", () => {
    for (const focus of [[], ["missing"]]) {
      const cy = narrowedGraph(900);
      const before = positions(cy);
      frameCamera(cy, focus, true);
      expect(inView(cy, ["a", "b", "c", "d", "lone"])).toBe(true);
      const { zoom, pan } = { zoom: cy.zoom(), pan: cy.pan() };
      cy.fit(cy.elements(), 40);
      expect(zoom).toBeCloseTo(cy.zoom(), 1);
      expect(pan.x).toBeCloseTo(cy.pan().x, -1);
      expect(positions(cy)).toEqual(before);
    }
  });

  it("jumps under reduced motion and eases over the theme's transition otherwise", () => {
    const jumped = narrowedGraph(600);
    const animate = vi.spyOn(jumped, "animate");
    frameCamera(jumped, ["c"], true);
    expect(animate).not.toHaveBeenCalled();

    const eased = narrowedGraph(600);
    const easing = vi.spyOn(eased, "animate").mockReturnValue(eased);
    frameCamera(eased, ["c"], false);
    expect(easing).toHaveBeenCalledExactlyOnceWith(
      { zoom: jumped.zoom(), pan: jumped.pan() },
      { duration: 240, easing: theme["transition-easing"] },
    );
    // The same target either way; only the way there differs.
    expect(eased.zoom()).toBe(0.5);
  });

  it("leaves the camera alone when there is nothing to frame or no room to frame it in", () => {
    const empty = cytoscape({ headless: true });
    graphs.push(empty);
    frameCamera(empty, ["c"], true);
    expect(empty.zoom()).toBe(1);

    const cramped = narrowedGraph(60);
    frameCamera(cramped, ["c"], true);
    expect(cramped.zoom()).toBe(0.5);
    expect(cramped.pan()).toEqual({ x: 20, y: 40 });
  });
});

describe("prefersReducedMotion", () => {
  it("follows the media query, and is false where there is none", () => {
    expect(prefersReducedMotion()).toBe(false);
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query === "(prefers-reduced-motion: reduce)",
    }));
    expect(prefersReducedMotion()).toBe(true);
  });
});
