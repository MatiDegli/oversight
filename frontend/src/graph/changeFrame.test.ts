import { afterEach, describe, expect, it, vi } from "vitest";
import * as theme from "../theme";

import { changeFrame, changeZoom } from "./changeFrame";
import type { ChangeFrameInput } from "./changeFrame";
import { nodesInViewport } from "./camera";

const changed = { id: "changed", x1: 900, y1: 700, x2: 1100, y2: 750 };
const near = { id: "near", x1: 500, y1: 400, x2: 700, y2: 450 };
const far = { id: "far", x1: 1100, y1: 1000, x2: 1300, y2: 1050 };
const floor = changeZoom().min;
afterEach(() => vi.restoreAllMocks());
const input: ChangeFrameInput = {
  nodes: [changed, near, far],
  changed: [changed.id],
  cone: [
    { node_id: near.id, distance: 1 },
    { node_id: far.id, distance: 2 },
  ],
  maxDepth: 3,
  frames: [],
  graph: { x1: 0, y1: 0, x2: 2400, y2: 2000 },
  size: { width: 680, height: 800 },
  area: { x1: 40, y1: 40, x2: 640, y2: 760 },
};
const frame = (options: Partial<ChangeFrameInput> = {}) => changeFrame({ ...input, ...options });
const visible = (options: Partial<ChangeFrameInput> = {}) => {
  const camera = frame(options);
  if (!camera) throw new Error("No camera");
  return nodesInViewport(options.nodes ?? input.nodes, camera, input.area);
};

describe("change framing", () => {
  it("takes only the zoom-out needed for the entire cone and centers its bounds", () => {
    expect(frame()).toEqual({ zoom: 0.75, pan: { x: -335, y: -143.75 } });
    expect(visible()).toEqual(new Set(["changed", "near", "far"]));
  });

  it("never magnifies above 100%, and respects the cone's depth", () => {
    expect(frame({ maxDepth: 0 })).toEqual({ zoom: 1, pan: { x: -660, y: -325 } });
    expect(frame({ maxDepth: 1 })?.zoom).toBe(1);
  });

  it("frames the change and distance 1 when the whole cone needs illegible labels", () => {
    const distant = { ...far, x1: 10000, x2: 10200 };
    const options = { nodes: [changed, near, distant], graph: { ...input.graph, x2: 11000 } };
    expect(frame(options)?.zoom).toBe(1);
    expect(visible(options)).toEqual(new Set(["changed", "near"]));
    expect(frame({ ...options, cone: [...input.cone].reverse() })).toEqual(frame(options));
  });

  it("holds at the legibility floor around the changes if no distance 1 member fits", () => {
    const distant = { ...near, x1: 10000, x2: 10200 };
    const options = { nodes: [changed, distant], cone: [{ node_id: near.id, distance: 1 }] };
    expect(frame(options)?.zoom).toBe(floor);
    expect(visible(options)).toEqual(new Set(["changed"]));
  });

  it("includes the whole cone exactly at the legibility boundary", () => {
    const edge = { ...far, x1: 500 + 600 / floor - 200, x2: 500 + 600 / floor };
    const options = { nodes: [changed, near, edge] };
    expect(frame(options)?.zoom).toBeCloseTo(floor);
    expect(visible(options)).toEqual(new Set(["changed", "near", "far"]));
  });

  it.each(["node-name-size", "node-word-size"] as const)(
    "derives the floor from the active %s token",
    (token) => {
      const active = theme.getActiveTheme();
      vi.spyOn(theme, "getActiveTheme").mockReturnValue({ ...active, [token]: "12px" });
      const distant = { ...near, x1: 10000, x2: 10200 };
      expect(
        frame({ nodes: [changed, distant], cone: [{ node_id: near.id, distance: 1 }] })?.zoom,
      ).toBe(11 / 12);
      expect(changeZoom().min).toBeGreaterThan(floor);
    },
  );

  it("keeps the nearest first-ring boxes that fit, regardless of cone order", () => {
    const nearest = { ...near, id: "nearest", x1: 700, x2: 900, y1: 700, y2: 750 };
    const next = { ...nearest, id: "next", x1: 500, x2: 700 };
    const opposite = { ...nearest, id: "opposite", x1: 1500, x2: 1700 };
    const distant = { ...nearest, id: "distant", x1: 10000, x2: 10200 };
    const options = {
      nodes: [changed, nearest, next, opposite, distant],
      cone: [distant, opposite, next, nearest].map((node) => ({ node_id: node.id, distance: 1 })),
    };
    expect(frame(options)?.zoom).toBe(floor);
    expect(visible(options)).toEqual(new Set([changed.id, nearest.id, next.id]));
    expect(frame({ ...options, cone: [...options.cone].reverse() })).toEqual(frame(options));
  });

  it("measures proximity to the nearest of multiple changes and keeps all changes clear", () => {
    const second = { ...changed, id: "second", x1: 1200, x2: 1300 };
    const adjacent = { ...second, id: "adjacent", x1: 1350, x2: 1400 };
    const distant = { ...near, x1: -10000, x2: -9800 };
    const options = {
      nodes: [changed, second, adjacent, distant],
      changed: [changed.id, second.id],
      cone: [adjacent, distant].map((node) => ({ node_id: node.id, distance: 1 })),
      area: { x1: 344, y1: 40, x2: 864, y2: 700 },
      size: { width: 904, height: 800 },
    };
    const camera = frame(options);
    if (!camera) throw new Error("Missing frame");
    expect(nodesInViewport(options.nodes, camera, options.area)).toEqual(
      new Set([changed.id, second.id, adjacent.id]),
    );
  });

  it("skips a nearby box that cannot fit and still includes the next fitting caller", () => {
    const oversized = { ...near, id: "oversized", x1: 1100, x2: 3000, y1: 700, y2: 750 };
    const options = {
      nodes: [changed, oversized, near],
      cone: [oversized, near].map((node) => ({ node_id: node.id, distance: 1 })),
    };
    expect(frame(options)?.zoom).toBe(floor);
    expect(visible(options)).toEqual(new Set([changed.id, near.id]));
  });

  it("projects hidden cone members and changes onto their folded class row", () => {
    const row = { id: "Class", x1: 500, y1: 400, x2: 782, y2: 470 };
    const representatives = new Map([
      ["Class.method", row.id],
      ["Class.other", row.id],
    ]);
    const options = {
      nodes: [changed, row],
      cone: [
        { node_id: "Class.method", distance: 1 },
        { node_id: "Class.other", distance: 2 },
      ],
      representatives,
    };
    expect(visible(options)).toEqual(new Set([changed.id, row.id]));
    expect(frame(options)).toEqual(frame({ ...options, cone: [{ node_id: row.id, distance: 1 }] }));
    expect(frame({ ...options, changed: ["Class.method"], cone: [] })).toEqual(
      frame({ ...options, changed: [row.id], cone: [] }),
    );
  });

  it("bounds the view within the graph and centers a graph smaller than the available area", () => {
    const origin = { ...changed, x1: 0, x2: 200, y1: 0, y2: 50 };
    expect(frame({ nodes: [origin], cone: [] })?.pan).toEqual({ x: 80, y: 80 });
    expect(frame({ nodes: [origin], cone: [], graph: origin })).toEqual({
      zoom: 1,
      pan: { x: 240, y: 375 },
    });
  });

  it("moves to the nearest gutter that keeps every module wholly in or out of view", () => {
    const module = { x1: 400, y1: 300, x2: 800, y2: 800 };
    const adjacent = { x1: 900, y1: 300, x2: 1200, y2: 800 };
    const options = {
      nodes: [near],
      changed: [near.id],
      cone: [],
      frames: [module, adjacent],
    };
    const camera = frame(options);
    if (!camera) throw new Error("Missing frame");
    expect(camera.zoom).toBe(1);
    for (const box of options.frames) {
      const b = {
        x1: box.x1 + camera.pan.x,
        x2: box.x2 + camera.pan.x,
        y1: box.y1 + camera.pan.y,
        y2: box.y2 + camera.pan.y,
      };
      expect(
        (b.x1 >= 40 && b.x2 <= 640 && b.y1 >= 40 && b.y2 <= 760) ||
          b.x2 <= 0 ||
          b.x1 >= 680 ||
          b.y2 <= 0 ||
          b.y1 >= 800,
      ).toBe(true);
    }
    expect(visible(options)).toEqual(new Set([near.id]));
  });

  it("reserves the measured overlay column and bottom overlays when fitting", () => {
    const area = { x1: 344, y1: 40, x2: 864, y2: 700 };
    const camera = frame({ size: { width: 904, height: 800 }, area });
    if (!camera) throw new Error("Missing frame");
    expect(camera.zoom).toBeCloseTo(520 / 600);
    expect(nodesInViewport(input.nodes, camera, area)).toEqual(new Set([changed.id, near.id]));
  });

  it("returns no target for empty, missing or cramped graphs and never mutates its input", () => {
    const before = structuredClone(input);
    expect(frame({ changed: [], cone: [] })).toBeNull();
    expect(frame({ changed: ["removed"], cone: [] })).toBeNull();
    expect(frame({ area: { x1: 80, y1: 0, x2: 40, y2: 10 } })).toBeNull();
    frame();
    expect(input).toEqual(before);
  });
});
