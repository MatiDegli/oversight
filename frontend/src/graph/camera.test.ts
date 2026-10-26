import cytoscape from "cytoscape";
import type { Core } from "cytoscape";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getActiveTheme } from "../theme";
import { frameCamera, prefersReducedMotion } from "./camera";

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

describe("frameCamera", () => {
  it("opening keeps the focused node and its neighbours in view, without moving a node", () => {
    const cy = narrowedGraph(600);
    const before = positions(cy);
    // Without a reframe the camera would stay put and these would sit past the 600 px edge.
    expect(inView(cy, ["c", "d"])).toBe(false);
    frameCamera(cy, "c", true);
    expect(inView(cy, ["b", "c", "d"])).toBe(true);
    expect(positions(cy)).toEqual(before);
    expect(cy.zoom()).toBeLessThanOrEqual(0.5);
  });

  it("never zooms in on open: an isolated node keeps the zoom it had", () => {
    const cy = narrowedGraph(600);
    frameCamera(cy, "lone", true);
    expect(cy.zoom()).toBe(0.5);
    expect(inView(cy, ["lone"])).toBe(true);
  });

  it("zooms out when the neighbourhood does not fit at the current zoom", () => {
    const cy = narrowedGraph(300);
    frameCamera(cy, "c", true);
    expect(cy.zoom()).toBeLessThan(0.5);
    expect(inView(cy, ["b", "c", "d"])).toBe(true);
  });

  it("closing, or opening with no node, returns to the fit of the whole graph", () => {
    for (const focus of [null, "missing"]) {
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
    frameCamera(jumped, "c", true);
    expect(animate).not.toHaveBeenCalled();

    const eased = narrowedGraph(600);
    const easing = vi.spyOn(eased, "animate").mockReturnValue(eased);
    frameCamera(eased, "c", false);
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
    frameCamera(empty, "c", true);
    expect(empty.zoom()).toBe(1);

    const cramped = narrowedGraph(60);
    frameCamera(cramped, "c", true);
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
