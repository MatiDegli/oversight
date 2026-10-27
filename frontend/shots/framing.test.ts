import assert from "node:assert/strict";
import { test } from "node:test";

import { assertPanelFrame, assertReadableFrame } from "./framing.ts";

const changed = { id: "changed", x1: 100, y1: 80, x2: 150, y2: 120 };
const near = { id: "caller", x1: 250, y1: 150, x2: 350, y2: 250, distance: 1 };
const frame = {
  zoom: 1,
  labelSizes: { name: 18, state: 16 },
  pan: { x: 75, y: 35 },
  size: { width: 600, height: 400 },
  focus: [changed],
  cone: [near],
};

void test("both panels must frame the change and cone; live has no review card or rows", () => {
  for (const mode of ["review", "live-diff"] as const) {
    assertPanelFrame(frame, mode);
    assert.throws(() => {
      assertPanelFrame({ ...frame, pan: { x: 300, y: 100 } }, mode);
    }, /outside/);
  }
  assert.throws(() => {
    assertPanelFrame({ ...frame, leftInset: 304 }, "live-diff");
  }, /review card/);
  assert.throws(() => {
    assertPanelFrame({ ...frame, outOfView: [near.id] }, "live-diff");
  }, /review rows/);
});

void test("shots requires the zoom needed for the full cone with legible labels", () => {
  assertReadableFrame(frame);
  assert.throws(() => {
    assertReadableFrame({ ...frame, zoom: 0.9 });
  }, /expected 1/);
  const narrower = { ...frame, size: { width: 300, height: 400 } };
  assertReadableFrame({ ...narrower, zoom: 0.88, pan: { x: -48, y: 54.8 } });
  assert.throws(() => {
    assertReadableFrame(narrower);
  }, /expected 0.88/);
});

void test("shots falls back to distance 1 if farther callers cannot fit legibly", () => {
  const far = { ...near, id: "far", x1: 2000, x2: 2050, distance: 2 };
  const fallback = { ...frame, cone: [near, far], outOfView: [far.id] };
  assertReadableFrame(fallback);
  assert.throws(() => {
    assertReadableFrame({ ...fallback, zoom: 0.4 });
  }, /11 CSS px/);
  assert.throws(() => {
    assertReadableFrame({ ...fallback, outOfView: [] });
  }, /actual visibility/);
});

void test("shots accepts the legibility floor when no distance 1 member fits", () => {
  const far = { ...near, id: "far", x1: 2000, x2: 2050 };
  const fallback = {
    ...frame,
    zoom: 11 / 16,
    pan: { x: 250, y: 160 },
    cone: [far],
    outOfView: [far.id],
  };
  assertReadableFrame(fallback);
  assert.throws(() => {
    assertReadableFrame({ ...fallback, zoom: 0.3 });
  }, /11 CSS px/);
});

void test("shots checks the clear area and the card's actual occlusion separately", () => {
  const card = { x1: 24, y1: 24, x2: 304, y2: 180 };
  const below = { id: "below", x1: -270, x2: -250, y1: 200, y2: 240, distance: 2 };
  const under = { ...below, id: "under", y1: 30, y2: 60 };
  const far = { ...near, id: "far", x1: 5000, x2: 5050, distance: 2 };
  const fallback = {
    ...frame,
    size: { width: 904, height: 400 },
    leftInset: 304,
    card,
    pan: { x: 379, y: 35 },
    cone: [near, below, under, far],
    outOfView: [under.id, far.id],
  };
  assertReadableFrame(fallback);
  assert.throws(() => {
    assertReadableFrame({ ...fallback, outOfView: [far.id] });
  }, /actual visibility/);
});
