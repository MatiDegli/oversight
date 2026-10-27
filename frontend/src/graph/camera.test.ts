import cytoscape from "cytoscape";
import type { Core } from "cytoscape";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getActiveTheme } from "../theme";
import {
  frameBox,
  frameCamera,
  includeNewNodes,
  nodesInViewport,
  prefersReducedMotion,
} from "./camera";

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

describe("frameBox", () => {
  const changed = { id: "change", x1: 100, y1: 80, x2: 150, y2: 120 };
  const near = { id: "near", x1: 200, y1: 80, x2: 250, y2: 120, distance: 1 };
  const next = { id: "next", x1: 300, y1: 80, x2: 350, y2: 120, distance: 1 };
  const far = { id: "far", x1: 2000, y1: 80, x2: 2050, y2: 120, distance: 1 };
  const farther = { id: "farther", x1: 400, y1: 80, x2: 450, y2: 120, distance: 2 };

  it("keeps watch zoom when all nodes fit and reports the complete framed set", () => {
    const camera = frameBox([changed], size, 1, limits, [near, next]);
    expect(camera).toEqual({
      zoom: 1,
      pan: { x: 75, y: 100 },
      framed: new Set(["change", "near", "next"]),
      outOfView: new Set(),
    });
  });

  it("shows farther callers that fit even when another distance-1 caller cannot fit", () => {
    const camera = frameBox([changed], size, 1, limits, [farther, far, next, near]);
    expect(camera).toEqual({
      zoom: 0.8,
      pan: { x: 200, y: 120 },
      framed: new Set(["change", "near", "next", "farther"]),
      outOfView: new Set(["far"]),
    });
  });

  it("chooses the larger distance-1 group on the demo-shaped opposite sides of the change", () => {
    const left = { ...near, id: "left", x1: -450, x2: -400 };
    const group = [600, 650, 700].map((x, i) => ({
      ...near,
      id: `right-${String(i)}`,
      x1: x,
      x2: x + 50,
    }));
    for (const cone of [[left, ...group], [...group].reverse().concat(left)]) {
      const camera = frameBox([changed], size, 1, limits, cone);
      expect(camera?.zoom).toBe(0.8);
      expect(camera?.pan).toEqual({ x: -40, y: 120 });
      expect(camera?.framed).toEqual(new Set(["change", ...group.map((node) => node.id)]));
      expect(camera?.outOfView).toEqual(new Set(["left"]));
    }
  });

  it("compares distance counts lexicographically, then uses distance 2 to break distance-1 ties", () => {
    const left = { ...near, id: "left", x1: -400, x2: -350 };
    const right = { ...near, id: "right", x1: 600, x2: 650 };
    const second = { ...left, id: "second", distance: 2 };
    const thirds = [1, 2, 3].map((i) => ({ ...right, id: `third-${String(i)}`, distance: 3 }));
    const camera = frameBox([changed], size, 1, limits, [right, left, second, ...thirds]);
    expect(camera?.framed).toEqual(new Set(["change", "left", "second"]));
    // Even many more farther callers cannot replace a single distance-1 caller.
    expect(
      frameBox([changed], size, 1, limits, [right, { ...left, distance: 2 }, second])?.framed,
    ).toEqual(new Set(["change", "right"]));
  });

  it("breaks equal counts by the pan closest to centering the change, then deterministically by x/y", () => {
    const left = { ...near, id: "left", x1: -300, x2: -250 };
    const right = { ...near, id: "right", x1: 600, x2: 650 };
    expect(frameBox([changed], size, 1, limits, [left, right])?.pan).toEqual({ x: 280, y: 120 });
    const symmetric = { ...left, x1: -400, x2: -350 };
    for (const cone of [
      [symmetric, right],
      [right, symmetric],
    ]) {
      expect(frameBox([changed], size, 1, limits, cone)?.pan).toEqual({ x: 40, y: 120 });
    }
  });

  it("sweeps both axes and keeps every changed box inside the fit area", () => {
    const second = { ...changed, id: "second", y1: 150, y2: 190 };
    const group = [0, 1].map((i) => ({
      ...near,
      id: `lower-${String(i)}`,
      x1: 600,
      x2: 650,
      y1: 400 + i * 20,
      y2: 420 + i * 20,
    }));
    const upper = { ...near, id: "upper", x1: -300, x2: -250, y1: -200, y2: -160 };
    const camera = frameBox([changed, second], size, 1, limits, [upper, ...group]);
    expect(camera?.framed).toEqual(new Set(["change", "second", "lower-0", "lower-1"]));
    expect(camera?.pan).toEqual({ x: 40, y: 8 });
  });

  it("centers an impossible changed set and reports actual visibility instead of a partial prefix", () => {
    const camera = frameBox([changed, { ...far, id: "second-change" }], size, 1, limits, [near]);
    expect(camera?.pan).toEqual({ x: -560, y: 120 });
    expect(camera?.framed).toEqual(new Set());
    expect(camera?.outOfView).toEqual(new Set(["change", "second-change", "near"]));
  });

  it("marks from the final visible rectangle, including nodes inside padding but outside the fit area", () => {
    const inPadding = { ...near, id: "padding", x1: 470, x2: 490 };
    const clipped = { ...near, id: "clipped", x1: 490, x2: 510 };
    const underCard = { ...near, id: "card", x1: -270, x2: -250 };
    const camera = frameBox(
      [changed],
      { width: 904, height: 400 },
      1,
      { ...limits, leftInset: 304, occlusion: { x1: 24, y1: 24, x2: 304, y2: 300 } },
      [
        far,
        { ...near, id: "anchor", x1: -200, x2: 450 },
        { ...inPadding, distance: 2 },
        { ...clipped, distance: 2 },
        underCard,
      ],
    );
    expect(camera?.framed.has("padding")).toBe(false);
    expect(camera?.outOfView.has("padding")).toBe(false);
    expect(camera?.outOfView.has("clipped")).toBe(true);
    expect(camera?.outOfView.has("card")).toBe(true);
  });

  it("requires whole boxes on every visible edge, with no padding added to visibility", () => {
    const nodes = [
      { id: "inside", x1: 304, y1: 0, x2: 600, y2: 400 },
      { id: "left", x1: 303, y1: 0, x2: 400, y2: 100 },
      { id: "right", x1: 500, y1: 0, x2: 601, y2: 100 },
      { id: "top", x1: 400, y1: -1, x2: 500, y2: 100 },
      { id: "bottom", x1: 400, y1: 300, x2: 500, y2: 401 },
    ];
    expect(
      nodesInViewport(
        nodes,
        { zoom: 1, pan: { x: 0, y: 0 } },
        { x1: 304, y1: 0, x2: 600, y2: 400 },
      ),
    ).toEqual(new Set(["inside"]));
    expect(
      nodesInViewport(
        nodes,
        { zoom: 0.8, pan: { x: 100, y: 40 } },
        { x1: 304, y1: 0, x2: 600, y2: 400 },
      ).size,
    ).toBe(5);
  });

  it("marks a node under the real card but keeps one below it visible in the same column", () => {
    const card = { x1: 24, y1: 24, x2: 304, y2: 433 };
    const nodes = [
      { id: "below", x1: 187, x2: 307, y1: 466, y2: 513 },
      { id: "under", x1: 187, x2: 307, y1: 380, y2: 427 },
    ];
    expect(
      nodesInViewport(
        nodes,
        { zoom: 1, pan: { x: 0, y: 0 } },
        { x1: 0, y1: 0, x2: 928, y2: 1028 },
        card,
      ),
    ).toEqual(new Set(["below"]));
  });

  it("excludes the cone card's width and inset plus padding from the fit area", () => {
    const camera = frameBox(
      [changed],
      { width: 904, height: 400 },
      1,
      { ...limits, leftInset: 280 + 24 },
      [near, next],
    );
    expect(camera?.zoom).toBe(1);
    expect(camera?.pan.x).toBe(379);
    for (const node of [changed, near, next]) {
      expect(node.x1 * (camera?.zoom ?? 0) + (camera?.pan.x ?? 0)).toBeGreaterThanOrEqual(344);
      expect(node.x2 * (camera?.zoom ?? 0) + (camera?.pan.x ?? 0)).toBeLessThanOrEqual(864);
    }
    const wide = { ...changed, x2: 750 };
    expect(
      frameBox([wide], { width: 904, height: 400 }, 1, { ...limits, leftInset: 304 })?.zoom,
    ).toBeCloseTo(0.8);
  });

  it("zooms out only as far as needed above the floor, considering label bounds on all sides", () => {
    const wide = frameBox([box(600, 50)], size, 1, limits);
    expect(wide?.zoom).toBeCloseTo(520 / 600);
    expect(100 * (wide?.zoom ?? 0) + (wide?.pan.x ?? 0)).toBeCloseTo(40);
    expect(frameBox([box(100, 380)], size, 1, limits)?.zoom).toBeCloseTo(320 / 380);
  });

  it("keeps labels at 12px even below the floor and marks an individually oversized change", () => {
    expect(frameBox([box(10, 10)], size, 0.5, limits)?.zoom).toBe(0.8);
    expect(frameBox([box(10, 10)], size, Infinity, limits)?.zoom).toBe(4);
    const oversized = frameBox([box(100_000, 10)], size, 1, limits);
    expect(oversized?.zoom).toBe(0.8);
    expect(oversized?.framed.size).toBe(0);
    expect(oversized?.outOfView).toEqual(new Set(["changed"]));
  });

  it("still fits the whole graph on close without the review floor", () => {
    expect(frameBox([box(2000, 50)], size, Infinity, { padding: 40, maxZoom: 4 })?.zoom).toBe(0.26);
  });

  it("returns the watch zoom and framing when the review card is removed from pure framing", () => {
    const nodes = [box(1000, 50)];
    const watch = frameBox(nodes, size, Infinity, { padding: 40, maxZoom: 4 });
    expect(watch?.zoom).toBe(0.52);
    expect(frameBox(nodes, size, Infinity, { ...limits, leftInset: 304 })?.zoom).toBe(0.8);
    const closed = frameBox(nodes, size, Infinity, { padding: 40, maxZoom: 4 });
    expect(closed).toEqual(watch);
  });

  it("returns null for an empty graph or no room after excluding the card and padding", () => {
    expect(frameBox([], size, 1, limits)).toBeNull();
    expect(
      frameBox([changed], { width: 384, height: 400 }, 1, { ...limits, leftInset: 304 }),
    ).toBeNull();
    expect(frameBox([changed], { width: 600, height: 60 }, 1, limits)).toBeNull();
  });
});

describe("frameCamera", () => {
  it("restores the exact saved watch camera after review, including a person's pan and zoom", () => {
    const cy = narrowedGraph(1600);
    const watch = { zoom: 1.017, pan: { x: -17, y: 52 } };
    cy.viewport(watch);
    const width = vi.spyOn(cy, "width").mockReturnValue(904);
    frameCamera(cy, ["a"], true, { coneIds: ["c", "d"], watchZoom: watch.zoom });
    expect(cy.zoom()).not.toBe(watch.zoom);
    width.mockReturnValue(1600);
    frameCamera(cy, [], true, { restoreCamera: watch });
    expect(cy.zoom()).toBe(watch.zoom);
    expect(cy.pan()).toEqual(watch.pan);
  });

  it("applies the review floor even to an empty cone, preserving live diff at a smaller watch zoom", () => {
    const cy = narrowedGraph(600);
    cy.zoom(0.5);
    frameCamera(cy, ["c"], true);
    expect(cy.zoom()).toBe(0.5);
    frameCamera(cy, ["c"], true, { coneIds: [], watchZoom: 0.5 });
    expect(cy.zoom()).toBe(0.8);
  });

  it("keeps the readable floor and the change centered when even the nearest distance cannot fit", () => {
    const cy = narrowedGraph(504);
    cy.minZoom(0.4);
    frameCamera(cy, ["a"], true, { coneIds: ["c", "d"] });
    expect(cy.zoom()).toBe(0.8);
    expect(cy.minZoom()).toBe(0.4);
    expect(inView(cy, ["a"])).toBe(true);
    expect(inView(cy, ["c", "d"])).toBe(false);
    expect(cy.getElementById("a").renderedPosition().x).toBeCloseTo(404);
  });
  it("maximizes complete callers at the floor including badges without moving nodes", () => {
    const cy = narrowedGraph(804);
    const before = positions(cy);
    cy.getElementById("b").data("distance", 1).style("bounds-expansion", 10);
    cy.getElementById("d").data("distance", 2);
    frameCamera(cy, ["a"], true, { coneIds: ["d", "b"], watchZoom: 1 });
    expect(inView(cy, ["a", "b"])).toBe(true);
    expect(inView(cy, ["d"])).toBe(false);
    expect(cy.zoom()).toBe(0.8);
    const bounds = cy
      .nodes()
      .filter((node) => ["a", "b"].includes(node.id()))
      .renderedBoundingBox();
    expect(bounds.x1).toBeGreaterThanOrEqual(344 - 0.5);
    expect(bounds.x2).toBeLessThanOrEqual(804 - 40 + 0.5);
    // With one visible caller, choose the closest pan to centering the change.
    expect(cy.getElementById("a").renderedPosition().x).toBeCloseTo(423.2);
    expect(positions(cy)).toEqual(before);
  });

  it("uses the original watch zoom when switching from a narrowed live diff to review", () => {
    const cy = narrowedGraph(904);
    cy.zoom(0.2);
    frameCamera(cy, ["c"], true, { coneIds: ["d"], watchZoom: 1 });
    expect(cy.zoom()).toBe(1);
    expect(inView(cy, ["c", "d"])).toBe(true);
  });

  it("frames available callers when the changed node is absent from main", () => {
    const cy = narrowedGraph(904);
    frameCamera(cy, ["removed"], true, { coneIds: ["c", "d"] });
    expect(cy.zoom()).toBe(1);
    expect(inView(cy, ["c", "d"])).toBe(true);
  });

  it("opening keeps the zoom the watch had and centers the lit nodes without moving a node", () => {
    const cy = narrowedGraph(600);
    const before = positions(cy);
    // Without a reframe the camera would stay put and these would sit past the 600 px edge.
    expect(inView(cy, ["c", "d"])).toBe(false);
    frameCamera(cy, ["c", "d"], true);
    expect(cy.zoom()).toBe(1);
    expect(inView(cy, ["c", "d"])).toBe(true);
    const lit = cy.collection().union(cy.getElementById("c")).union(cy.getElementById("d"));
    const { x1, x2 } = lit.renderedBoundingBox({ includeLabels: true });
    expect((x1 + x2) / 2).toBeCloseTo(300);
    expect(positions(cy)).toEqual(before);
  });

  it("lets the context around the lit nodes run past the edges", () => {
    const cy = narrowedGraph(600);
    frameCamera(cy, ["c"], true);
    expect(cy.zoom()).toBe(1);
    expect(inView(cy, ["c"])).toBe(true);
    // The neighbours are not framed: the one at the far right is cut off, and nothing zoomed out.
    expect(inView(cy, ["d", "lone"])).toBe(false);
  });

  it("zooms out only when the lit nodes do not fit at the zoom it had", () => {
    const cy = narrowedGraph(460);
    frameCamera(cy, ["c", "d"], true);
    expect(cy.zoom()).toBeLessThan(1);
    expect(cy.zoom()).toBeGreaterThanOrEqual(0.8);
    const { x1, x2 } = cy
      .collection()
      .union(cy.getElementById("c"))
      .union(cy.getElementById("d"))
      .renderedBoundingBox({ includeLabels: true });
    expect(x1).toBeGreaterThanOrEqual(40 - 0.5);
    expect(x2).toBeLessThanOrEqual(460 - 40 + 0.5);
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
    expect(eased.zoom()).toBe(1);
  });

  it("leaves the camera alone when there is nothing to frame or no room to frame it in", () => {
    const empty = cytoscape({ headless: true });
    graphs.push(empty);
    frameCamera(empty, ["c"], true);
    expect(empty.zoom()).toBe(1);

    const cramped = narrowedGraph(60);
    frameCamera(cramped, ["c"], true);
    expect(cramped.zoom()).toBe(1);
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

describe("watch growth", () => {
  it.each([true, false])(
    "includes an offscreen addition with the minimum centred zoom-out (reduced: %s)",
    (reduced) => {
      const cy = narrowedGraph(600);
      const before = structuredClone(positions(cy));
      const zoom = cy.zoom();
      const pan = { ...cy.pan() };
      const center = { x: (300 - pan.x) / zoom, y: (300 - pan.y) / zoom };
      const box = cy
        .getElementById("lone")
        .boundingBox({ includeLabels: true, includeOverlays: false });
      const animate = vi.spyOn(cy, "animate").mockReturnValue(cy);
      includeNewNodes(cy, ["lone"], reduced);
      if (!reduced) {
        expect(animate).toHaveBeenCalledOnce();
        const target = animate.mock.calls[0]?.[0];
        if (!target) throw new Error("Missing target");
        if (typeof target.zoom !== "number" || !target.pan) throw new Error("Missing viewport");
        cy.viewport({ zoom: target.zoom, pan: target.pan });
        expect(animate.mock.calls[0]?.[1]).toEqual({
          duration: 240,
          easing: theme["transition-easing"],
        });
      } else expect(animate).not.toHaveBeenCalled();
      expect(cy.zoom()).toBeLessThan(zoom);
      expect((300 - cy.pan().x) / cy.zoom()).toBeCloseTo(center.x);
      expect((300 - cy.pan().y) / cy.zoom()).toBeCloseTo(center.y);
      expect(box.x2 * cy.zoom() + cy.pan().x).toBeCloseTo(560);
      expect(cy.getElementById("lone").renderedBoundingBox().x1).toBeGreaterThanOrEqual(40);
      expect(positions(cy)).toEqual(before);
      expect(cy.nodes(":selected")).toHaveLength(0);
    },
  );

  it("leaves the camera alone for additions already in view, missing nodes and a cramped viewport", () => {
    const cy = narrowedGraph(600);
    const viewport = { zoom: cy.zoom(), pan: { ...cy.pan() } };
    const animate = vi.spyOn(cy, "animate");
    includeNewNodes(cy, ["b"], false);
    includeNewNodes(cy, ["missing"], false);
    vi.spyOn(cy, "width").mockReturnValue(60);
    includeNewNodes(cy, ["lone"], false);
    expect(animate).not.toHaveBeenCalled();
    expect({ zoom: cy.zoom(), pan: cy.pan() }).toEqual(viewport);
  });
});
