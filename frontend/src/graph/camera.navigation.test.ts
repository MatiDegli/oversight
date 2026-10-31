import cytoscape from "cytoscape";
import { describe, expect, it, vi } from "vitest";

import {
  frameCamera,
  hubOf,
  includeNewNodes,
  revealNode,
  startCamera,
  startingView,
  ZOOM_RANGE,
  zoomBy,
} from "./camera";
import * as changeFrameModule from "./changeFrame";
import { changeZoom } from "./changeFrame";

import { graphs, inView, narrowedGraph, positions, theme, wideGraph } from "./camera.support";

describe("watch growth", () => {
  it.each([
    [true, undefined],
    [false, undefined],
    [true, 0],
    [false, 0],
  ] as const)(
    "includes an offscreen addition with the minimum centred zoom-out (reduced: %s, covered: %s)",
    (reduced, covered) => {
      const cy = narrowedGraph(600);
      const before = structuredClone(positions(cy));
      const zoom = cy.zoom();
      const pan = { ...cy.pan() };
      const center = { x: (300 - pan.x) / zoom, y: (300 - pan.y) / zoom };
      const box = cy
        .getElementById("lone")
        .boundingBox({ includeLabels: true, includeOverlays: false });
      const animate = vi.spyOn(cy, "animate").mockReturnValue(cy);
      includeNewNodes(cy, ["lone"], reduced, covered);
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

  it.each([undefined, 0, 200])(
    "uses only the uncovered width for new nodes (covered=%s)",
    (covered) => {
      const cy = narrowedGraph(600);
      const before = { zoom: cy.zoom(), pan: { ...cy.pan() } };
      const center = { x: (600 - (covered ?? 0)) / 2, y: 300 };
      const anchor = (center.x - before.pan.x) / before.zoom;
      includeNewNodes(cy, ["b"], true, covered);
      if (!covered) expect({ zoom: cy.zoom(), pan: cy.pan() }).toEqual(before);
      else {
        expect(cy.zoom()).toBeLessThan(before.zoom);
        const box = cy.getElementById("b").renderedBoundingBox();
        expect(box.x1).toBeGreaterThanOrEqual(0);
        expect(box.x2).toBeLessThanOrEqual(600 - covered);
        expect((center.x - cy.pan().x) / cy.zoom()).toBeCloseTo(anchor);
      }
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

describe("zoomBy", () => {
  it("zooms about the middle of the view and moves no node", () => {
    const cy = narrowedGraph(1000);
    const before = positions(cy);
    // The model point under the middle of the view (500, 300) at zoom 1 and pan (20, 40).
    zoomBy(cy, 2, true);
    expect(cy.zoom()).toBe(2);
    expect(cy.pan()).toEqual({ x: 500 + (20 - 500) * 2, y: 300 + (40 - 300) * 2 });
    expect(positions(cy)).toEqual(before);
    zoomBy(cy, 0.5, true);
    expect(cy.zoom()).toBeCloseTo(1);
    expect(cy.pan().x).toBeCloseTo(20);
    expect(cy.pan().y).toBeCloseTo(40);
  });

  it("stays inside the zoom range, however far the person keeps pressing", () => {
    const cy = narrowedGraph(1000);
    for (let press = 0; press < 40; press++) zoomBy(cy, 1.25, true);
    expect(cy.zoom()).toBe(ZOOM_RANGE.max);
    for (let press = 0; press < 80; press++) zoomBy(cy, 0.8, true);
    expect(cy.zoom()).toBe(ZOOM_RANGE.min);
  });
});

describe("the zoom range after Fit", () => {
  it("keeps the range when the graph is too wide to fit in it: Fit stops at the range's lower end", () => {
    // 40,000 wide: fitted whole in 1,000px it would be about 2.5%.
    const cy = wideGraph(40_000);
    const framing = frameCamera(cy, [], true);
    expect(cy.zoom()).toBe(ZOOM_RANGE.min);
    expect(cy.minZoom()).toBe(ZOOM_RANGE.min);
    // What Fit reports is what it did.
    expect(framing?.zoom).toBe(ZOOM_RANGE.min);
    // The middle of the graph is the middle of the view.
    expect((500 - cy.pan().x) / cy.zoom()).toBeCloseTo(20_000, 0);
  });

  it("holds the range for the zoom buttons that follow Fit, however far they are pressed", () => {
    const cy = wideGraph(40_000);
    frameCamera(cy, [], true);
    for (let press = 0; press < 20; press++) zoomBy(cy, 0.8, true);
    expect(cy.zoom()).toBe(ZOOM_RANGE.min);
    expect(cy.minZoom()).toBe(ZOOM_RANGE.min);
    zoomBy(cy, 2, true);
    expect(cy.zoom()).toBeCloseTo(ZOOM_RANGE.min * 2);
    expect(cy.minZoom()).toBe(ZOOM_RANGE.min);
  });

  it("still fits a graph that fits inside the range, and zooms out from there", () => {
    const cy = wideGraph(2_000);
    frameCamera(cy, [], true);
    expect(cy.zoom()).toBeGreaterThan(ZOOM_RANGE.min);
    expect(cy.zoom()).toBeLessThan(1);
    zoomBy(cy, 0.1, true);
    expect(cy.zoom()).toBe(ZOOM_RANGE.min);
    expect(cy.minZoom()).toBe(ZOOM_RANGE.min);
  });

  it.each([0, 200])(
    "keeps growth above the zoom floor about the clear centre (covered=%s)",
    (covered) => {
      const cy = wideGraph(40_000);
      cy.viewport({ zoom: ZOOM_RANGE.min, pan: { x: 20, y: 40 } });
      const centerX = (1000 - covered) / 2;
      const middle = { x: (centerX - 20) / ZOOM_RANGE.min, y: (300 - 40) / ZOOM_RANGE.min };
      // Well past what the view shows at the lower end of the range.
      cy.add({ data: { id: "far" }, position: { x: 400_000, y: 100 } });
      includeNewNodes(cy, ["far"], true, covered);
      expect(cy.zoom()).toBe(ZOOM_RANGE.min);
      expect(cy.minZoom()).toBe(ZOOM_RANGE.min);
      expect((centerX - cy.pan().x) / cy.zoom()).toBeCloseTo(middle.x);
      expect((300 - cy.pan().y) / cy.zoom()).toBeCloseTo(middle.y);
    },
  );
});

describe("revealNode", () => {
  const inset =
    Number.parseFloat(theme["cone-card-width"]) + Number.parseFloat(theme["spacing-overlay"]);

  it("pans to put a node in the middle of what the cone card leaves free, at the zoom it has", () => {
    const cy = wideGraph(40_000);
    cy.viewport({ zoom: 0.5, pan: { x: 20, y: 40 } });
    const before = positions(cy);
    const target = revealNode(cy, "east", true);
    expect(target?.zoom).toBe(0.5);
    expect(cy.zoom()).toBe(0.5);
    const { x, y } = cy.getElementById("east").renderedPosition();
    expect(x).toBeCloseTo((inset + 1000) / 2);
    expect(y).toBeCloseTo(300);
    expect(cy.pan()).toEqual(target?.pan);
    expect(positions(cy)).toEqual(before);
  });

  it("eases under normal motion and does nothing for a node that is not on the graph", () => {
    const cy = wideGraph(40_000);
    const animate = vi.spyOn(cy, "animate").mockReturnValue(cy);
    expect(revealNode(cy, "missing", false)).toBeNull();
    expect(animate).not.toHaveBeenCalled();
    revealNode(cy, "east", false);
    expect(animate).toHaveBeenCalledOnce();
    expect(animate.mock.calls[0]?.[0]).toMatchObject({ zoom: 1 });
  });

  it("is a move like any other: it keeps the zoom range", () => {
    const cy = wideGraph(40_000);
    cy.viewport({ zoom: ZOOM_RANGE.min, pan: { x: 0, y: 0 } });
    revealNode(cy, "east", true);
    expect(cy.zoom()).toBe(ZOOM_RANGE.min);
    expect(cy.minZoom()).toBe(ZOOM_RANGE.min);
  });
});

describe("startingView", () => {
  const graph = { x1: 0, y1: 0, x2: 4000, y2: 3000 };
  const size = { width: 1000, height: 800 };

  it("is 100% and centers the focus where the graph is larger than the view", () => {
    const camera = startingView(graph, { x1: 1900, y1: 1400, x2: 2100, y2: 1600 }, size, 40);
    expect(camera.zoom).toBe(1);
    // The focus's middle (2000, 1500) is the middle of the view.
    expect(camera.pan).toEqual({ x: -(2000 - 500), y: -(1500 - 400) });
  });

  it("stays inside the graph, with the padding to spare, at its edges", () => {
    const corner = startingView(graph, { x1: 0, y1: 0, x2: 100, y2: 100 }, size, 40);
    expect(corner.pan).toEqual({ x: 40, y: 40 });
    const far = startingView(graph, { x1: 3900, y1: 2900, x2: 4000, y2: 3000 }, size, 40);
    expect(far.pan).toEqual({ x: -(4040 - 1000), y: -(3040 - 800) });
  });

  it("leaves out a frame the view's edge would cut, when the focus stays in view", () => {
    const frames = [
      { x1: 0, y1: 0, x2: 300, y2: 500 },
      { x1: 420, y1: 0, x2: 720, y2: 500 },
      { x1: 840, y1: 0, x2: 1140, y2: 500 },
    ];
    const wide = { x1: 0, y1: 0, x2: 1140, y2: 500 };
    const narrow = { width: 900, height: 800 };
    // Clamped at the right, the view would start at 280, 20px into the first frame.
    const clamped = startingView(wide, frames[2] as never, narrow, 40);
    expect(clamped.pan.x).toBe(-(1180 - 900));
    const snapped = startingView(wide, frames[2] as never, narrow, 40, frames);
    // The first frame goes whole; the nearest gutter starts at its trailing edge.
    expect(snapped.pan.x).toBe(-300);
    // A focus that the move would push out of view keeps its centered camera, cut or not.
    const focus = { x1: 100, y1: 0, x2: 1100, y2: 100 };
    expect(startingView(wide, focus, narrow, 40, frames).pan.x).toBe(-150);
  });

  it("centers the graph on an axis where it is smaller than the view, and never zooms", () => {
    const small = { x1: 0, y1: 0, x2: 400, y2: 3000 };
    const camera = startingView(small, { x1: 0, y1: 0, x2: 100, y2: 100 }, size, 40);
    expect(camera.zoom).toBe(1);
    expect(camera.pan.x).toBe(-(400 - 1000) / 2);
    expect(camera.pan.y).toBe(40);
  });
});

describe("hubOf", () => {
  const graphOf = (edges: [string, string, string?][]) => {
    const cy = cytoscape({
      headless: true,
      layout: { name: "preset" },
      elements: [
        ...["a", "b", "c", "d"].map((id) => ({
          data: { id },
          classes: "function",
          position: { x: 0, y: 0 },
        })),
        { data: { id: "m" }, classes: "module", position: { x: 0, y: 0 } },
        ...edges.map(([source, target, kind], i) => ({
          data: { id: `e${String(i)}`, source, target },
          classes: kind ?? "calls",
        })),
      ],
    });
    graphs.push(cy);
    return cy;
  };

  it("is the function that the most calls reach, the first by id on a tie", () => {
    expect(
      hubOf(
        graphOf([
          ["a", "c"],
          ["b", "c"],
          ["d", "c"],
          ["a", "b"],
          ["c", "d"],
        ]),
      ),
    ).toBe("c");
    expect(
      hubOf(
        graphOf([
          ["a", "d"],
          ["b", "d"],
          ["a", "c"],
          ["b", "c"],
        ]),
      ),
    ).toBe("c");
  });

  it("counts calls only, and says nothing when nothing is called", () => {
    expect(
      hubOf(
        graphOf([
          ["a", "m", "imports"],
          ["b", "m", "imports"],
          ["a", "b", "inherits"],
        ]),
      ),
    ).toBeNull();
    expect(hubOf(graphOf([]))).toBeNull();
  });

  it("does not count an import that names a function, even when it outnumbers the calls", () => {
    expect(
      hubOf(
        graphOf([
          ["a", "b", "imports"],
          ["c", "b", "imports"],
          ["d", "b", "imports"],
          ["a", "d"],
        ]),
      ),
    ).toBe("d");
  });
});

describe("startCamera", () => {
  function graphForStart() {
    const cy = narrowedGraph(1000);
    vi.spyOn(cy, "height").mockReturnValue(800);
    return cy;
  }

  it("starts at the same 0.83 whole-graph framing as Fit, including labels and clear area", () => {
    const cy = graphForStart();
    const whole = cy.nodes().boundingBox({ includeLabels: true, includeOverlays: false });
    const width = (whole.x2 - whole.x1) * 0.83;
    vi.spyOn(cy, "width").mockReturnValue(width + 464);
    const area = { x1: 24, y1: 84, x2: width + 40, y2: 730 };
    startCamera(cy, area);
    const start = { zoom: cy.zoom(), pan: cy.pan() };
    expect(start.zoom).toBeCloseTo(0.83, 8);
    frameCamera(cy, [], true, { area: { ...area, x1: 40 } });
    expect(cy.zoom()).toBeCloseTo(start.zoom, 8);
    expect(cy.pan()).toEqual(start.pan);
    expect(
      inView(
        cy,
        cy.nodes().map((node) => node.id()),
      ),
    ).toBe(true);
  });

  it("retains the centered 100% start when the whole graph fits without shrinking", () => {
    const cy = graphForStart();
    vi.spyOn(cy, "width").mockReturnValue(3000);
    startCamera(cy);
    expect(cy.zoom()).toBe(1);
    const b = cy.nodes().renderedBoundingBox({ includeLabels: false });
    expect((b.x1 + b.x2) / 2).toBeCloseTo(1500);
  });

  it("reads its floor from changeZoom's active typography rather than a copied constant", () => {
    const active = vi
      .spyOn(changeFrameModule, "changeZoom")
      .mockReturnValue(changeZoom({ "node-name-size": "20px", "node-word-size": "20px" }));
    try {
      const cy = graphForStart();
      vi.spyOn(cy, "width").mockReturnValue(400);
      startCamera(cy);
      expect(cy.zoom()).toBe(changeZoom().min);
    } finally {
      active.mockRestore();
    }
  });

  it("uses the shared legibility floor on the hub when the whole graph cannot fit", () => {
    const cy = cytoscape({
      headless: true,
      styleEnabled: true,
      layout: { name: "preset" },
      style: [{ selector: "node", style: { width: 100, height: 40, shape: "rectangle" } }],
      elements: [
        { data: { id: "m1" }, classes: "module" },
        { data: { id: "m2" }, classes: "module" },
        { data: { id: "m3" }, classes: "module" },
        { data: { id: "a", parent: "m1" }, classes: "function", position: { x: 0, y: 0 } },
        { data: { id: "hub", parent: "m2" }, classes: "function", position: { x: 3000, y: 2000 } },
        { data: { id: "z", parent: "m3" }, classes: "function", position: { x: 6000, y: 4000 } },
        { data: { id: "e1", source: "a", target: "hub" }, classes: "calls" },
        { data: { id: "e2", source: "z", target: "hub" }, classes: "calls" },
      ],
    });
    graphs.push(cy);
    vi.spyOn(cy, "width").mockReturnValue(1000);
    vi.spyOn(cy, "height").mockReturnValue(800);
    cy.zoom(0.3);
    startCamera(cy);
    expect(cy.zoom()).toBe(changeZoom().min);
    // The hub's module is at the middle of the view.
    const box = cy.getElementById("m2").renderedBoundingBox({ includeLabels: false });
    expect((box.x1 + box.x2) / 2).toBeCloseTo(500, 0);
    expect((box.y1 + box.y2) / 2).toBeCloseTo(400, 0);
    // No node moved.
    expect(cy.getElementById("hub").position()).toEqual({ x: 3000, y: 2000 });
  });

  it("starts between frames: no outermost frame is cut by the view's left or right edge", () => {
    const module = (id: string, x: number) => [
      { data: { id }, classes: "module" },
      { data: { id: `${id}:f`, parent: id }, classes: "function", position: { x, y: 0 } },
    ];
    const cy = cytoscape({
      headless: true,
      styleEnabled: true,
      layout: { name: "preset" },
      style: [{ selector: "node", style: { width: 100, height: 40, shape: "rectangle" } }],
      elements: [
        ...module("m1", 0),
        ...module("m2", 400),
        ...module("m3", 800),
        { data: { id: "e", source: "m1:f", target: "m3:f" }, classes: "calls" },
        { data: { id: "e2", source: "m2:f", target: "m3:f" }, classes: "calls" },
      ],
    });
    graphs.push(cy);
    vi.spyOn(cy, "width").mockReturnValue(560);
    vi.spyOn(cy, "height").mockReturnValue(400);
    startCamera(cy);
    expect(cy.zoom()).toBe(changeZoom().min);
    for (const id of ["m1", "m2", "m3"]) {
      const box = cy.getElementById(id).renderedBoundingBox({ includeLabels: false });
      for (const edge of [0, 560])
        expect(box.x1 < edge && box.x2 > edge, `${id} at ${String(edge)}`).toBe(false);
    }
    // The hub's own frame is whole and in view.
    const hub = cy.getElementById("m3").renderedBoundingBox({ includeLabels: false });
    expect(hub.x1).toBeGreaterThanOrEqual(0);
    expect(hub.x2).toBeLessThanOrEqual(560);
  });
});
