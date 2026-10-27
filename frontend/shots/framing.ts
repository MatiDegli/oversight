import assert from "node:assert/strict";

import type { NodeBounds } from "../src/graph";

type Box = Pick<NodeBounds, "x1" | "y1" | "x2" | "y2">;
interface Frame {
  zoom: number;
  labelSizes: { name: number; state: number };
  pan: { x: number; y: number };
  size: { width: number; height: number };
  leftInset?: number;
  card?: Box;
  area?: Box;
  focus: NodeBounds[];
  cone: NodeBounds[];
  outOfView?: string[];
}

/** Both panels frame their opening cone; only review adds a card and out-of-view rows. */
export function assertPanelFrame(frame: Frame, mode: "review" | "live-diff"): void {
  if (mode === "live-diff") {
    assert(!frame.card && !frame.leftInset, "live diff must not show the review card");
    assert(!frame.outOfView?.length, "live diff must not report review rows");
  }
  // Only review has rows to mark. Live diff still checks legibility and all selected boxes.
  assertReadableFrame(mode === "live-diff" ? { ...frame, outOfView: undefined } : frame);
}

/** Independent geometry acceptance check, shared by capture scenes and their unit tests. */
export function assertReadableFrame(frame: Frame): void {
  const area = frame.area ?? {
    x1: (frame.leftInset ?? 0) + 40,
    y1: 40,
    x2: frame.size.width - 40,
    y2: frame.size.height - 40,
  };
  const room = { width: area.x2 - area.x1, height: area.y2 - area.y1 };
  assert(room.width > 0 && room.height > 0, "no unobscured fit area");
  const fit = (nodes: NodeBounds[]) =>
    Math.min(
      room.width /
        Math.max(1, Math.max(...nodes.map((n) => n.x2)) - Math.min(...nodes.map((n) => n.x1))),
      room.height /
        Math.max(1, Math.max(...nodes.map((n) => n.y2)) - Math.min(...nodes.map((n) => n.y1))),
    );
  let targets = [...frame.focus, ...frame.cone];
  assert(targets.length > 0, "no nodes to frame");
  const floor = 11 / Math.min(frame.labelSizes.name, frame.labelSizes.state);
  assert(frame.zoom >= floor - 1e-6, "labels must render at least 11 CSS px");
  if (fit(targets) < floor - 1e-7)
    targets = [...frame.focus, ...frame.cone.filter((n) => (n.distance ?? 1) === 1)];
  const partial = fit(targets) < floor - 1e-7;
  const expectedZoom = partial ? floor : Math.max(floor, Math.min(1, fit(targets)));
  assert(
    Math.abs(frame.zoom - expectedZoom) < 1e-6,
    `zoom ${String(frame.zoom)}; expected ${String(expectedZoom)}`,
  );
  const inside = (node: NodeBounds, bounds: Box) =>
    node.x1 * frame.zoom + frame.pan.x >= bounds.x1 - 1e-6 &&
    node.x2 * frame.zoom + frame.pan.x <= bounds.x2 + 1e-6 &&
    node.y1 * frame.zoom + frame.pan.y >= bounds.y1 - 1e-6 &&
    node.y2 * frame.zoom + frame.pan.y <= bounds.y2 + 1e-6;
  if (partial) targets = nearestTargets(frame, fit, floor);
  if (targets.length && fit(targets) >= floor - 1e-7)
    assert(
      targets.every((node) => inside(node, area)),
      "target outside the clear area",
    );
  if (frame.outOfView) {
    const card = frame.card;
    const visible = (node: NodeBounds) =>
      inside(node, {
        x1: 0,
        y1: 0,
        x2: frame.size.width,
        y2: frame.size.height,
      }) &&
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
      "out-of-view card marks must match actual visibility",
    );
  }
}

/** Independently require each nearest caller that can join the already selected boxes. */
function nearestTargets(frame: Frame, fit: (nodes: NodeBounds[]) => number, floor: number) {
  const gap = (node: NodeBounds) =>
    Math.min(
      ...frame.focus.map((change) =>
        Math.hypot(
          Math.max(0, node.x1 - change.x2, change.x1 - node.x2),
          Math.max(0, node.y1 - change.y2, change.y1 - node.y2),
        ),
      ),
    );
  const targets = [...frame.focus];
  for (const node of frame.cone
    .filter((n) => (n.distance ?? 1) === 1)
    .sort((a, b) => gap(a) - gap(b) || a.id.localeCompare(b.id))) {
    if (fit([...targets, node]) >= floor - 1e-7) targets.push(node);
  }
  return targets;
}
