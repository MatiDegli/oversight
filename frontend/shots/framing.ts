import assert from "node:assert/strict";

import type { NodeBounds } from "../src/graph";

interface Frame {
  zoom: number;
  pan: { x: number; y: number };
  size: { width: number; height: number };
  /** Measured right edge of the cone card relative to the graph's left edge. */
  leftInset?: number;
  card?: { x1: number; y1: number; x2: number; y2: number };
  focus: NodeBounds[];
  cone: NodeBounds[];
  outOfView?: string[];
}

/** Independent acceptance oracle in model coordinates, shared by browser tests and make shots. */
export function assertReadableFrame(watchZoom: number, frame: Frame): void {
  const floor = 12 / 15;
  const left = (frame.leftInset ?? 0) + 40;
  const right = frame.size.width - 40;
  const room = { width: right - left, height: frame.size.height - 80 };
  assert(room.width > 0 && room.height > 0, "no unobscured fit area");
  const bounds = (nodes: NodeBounds[]) => ({
    x1: Math.min(...nodes.map((node) => node.x1)),
    y1: Math.min(...nodes.map((node) => node.y1)),
    x2: Math.max(...nodes.map((node) => node.x2)),
    y2: Math.max(...nodes.map((node) => node.y2)),
  });
  const focusIds = new Set(frame.focus.map((node) => node.id));
  const callers = frame.cone.filter((node) => !focusIds.has(node.id));
  const lit = [...frame.focus, ...callers];
  assert(lit.length > 0, "no lit nodes to frame");
  const whole = bounds(lit);
  const fullFit = Math.min(
    room.width / Math.max(whole.x2 - whole.x1, 1),
    room.height / Math.max(whole.y2 - whole.y1, 1),
  );
  const expectedZoom = Math.max(floor, Math.min(watchZoom, fullFit));
  assert(frame.zoom >= floor - 1e-6, `labels below 12px: zoom ${String(frame.zoom)}`);
  assert(
    Math.abs(frame.zoom - expectedZoom) < 1e-6,
    `zoom ${String(frame.zoom)}; expected ${String(expectedZoom)} (watch ${String(watchZoom)}, lit fit ${String(fullFit)})`,
  );
  // Represent the fit area as a model-space sliding window, independently of the product's pan sweep.
  const width = room.width / frame.zoom;
  const height = room.height / frame.zoom;
  const actual = { x: (left - frame.pan.x) / frame.zoom, y: (40 - frame.pan.y) / frame.zoom };
  const contains = (node: NodeBounds, origin: { x: number; y: number }) =>
    node.x1 >= origin.x - 1e-6 &&
    node.x2 <= origin.x + width + 1e-6 &&
    node.y1 >= origin.y - 1e-6 &&
    node.y2 <= origin.y + height + 1e-6;
  const target = fullFit >= floor ? whole : bounds(frame.focus.length ? frame.focus : lit);
  const preferred = {
    x: (target.x1 + target.x2 - width) / 2,
    y: (target.y1 + target.y2 - height) / 2,
  };
  if (fullFit >= floor || target.x2 - target.x1 > width || target.y2 - target.y1 > height) {
    assert(
      Math.abs(actual.x - preferred.x) * frame.zoom <= 2 &&
        Math.abs(actual.y - preferred.y) * frame.zoom <= 2,
      "frame off center",
    );
  } else {
    assert(
      frame.focus.every((node) => contains(node, actual)),
      "changed box outside fit area",
    );
    const distances = [...new Set(callers.map((node) => node.distance ?? Infinity))].sort(
      (a, b) => a - b,
    );
    const score = (origin: { x: number; y: number }) =>
      distances.map(
        (distance) =>
          callers.filter(
            (node) => (node.distance ?? Infinity) === distance && contains(node, origin),
          ).length,
      );
    const actualScore = score(actual);
    const offset = (origin: { x: number; y: number }) =>
      (origin.x - preferred.x) ** 2 + (origin.y - preferred.y) ** 2;
    const axes = (axis: "x" | "y", start: "x1" | "y1", end: "x2" | "y2", span: number) => {
      const low = frame.focus.length ? target[end] - span : -Infinity;
      const high = frame.focus.length ? target[start] : Infinity;
      return [
        ...new Set(
          [preferred[axis], ...lit.flatMap((node) => [node[start], node[end] - span])].map(
            (value) => Math.min(high, Math.max(low, value)),
          ),
        ),
      ];
    };
    for (const x of axes("x", "x1", "x2", width)) {
      for (const y of axes("y", "y1", "y2", height)) {
        const candidate = { x, y };
        const otherScore = score(candidate);
        const first = otherScore.findIndex((count, i) => count !== actualScore[i]);
        assert(
          first < 0 || (otherScore[first] ?? 0) < (actualScore[first] ?? 0),
          "a pan shows more callers by distance",
        );
        if (first < 0) {
          assert(
            offset(actual) <= offset(candidate) + 1e-5,
            "equal counts have a pan closer to centering the change",
          );
        }
      }
    }
  }
  if (frame.outOfView) {
    const card = frame.card;
    const visible = (node: NodeBounds) =>
      node.x1 * frame.zoom + frame.pan.x >= -1e-6 &&
      node.x2 * frame.zoom + frame.pan.x <= frame.size.width + 1e-6 &&
      node.y1 * frame.zoom + frame.pan.y >= -1e-6 &&
      node.y2 * frame.zoom + frame.pan.y <= frame.size.height + 1e-6 &&
      (!card ||
        node.x2 * frame.zoom + frame.pan.x <= card.x1 + 1e-6 ||
        node.x1 * frame.zoom + frame.pan.x >= card.x2 - 1e-6 ||
        node.y2 * frame.zoom + frame.pan.y <= card.y1 + 1e-6 ||
        node.y1 * frame.zoom + frame.pan.y >= card.y2 - 1e-6);
    assert.deepEqual(
      [...frame.outOfView].sort(),
      frame.cone
        .filter((node) => !visible(node))
        .map((node) => node.id)
        .sort(),
      "out-of-view card marks must match the final viewport without fit padding",
    );
  }
}
