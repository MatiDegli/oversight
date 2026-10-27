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
  graph: [{ id: "graph", x1: -1000, y1: -1000, x2: 6000, y2: 6000 }],
  modules: [],
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
  // The live diff has the same fallback camera, but no review rows to mark off-screen callers.
  assertPanelFrame({ ...fallback, outOfView: [] }, "live-diff");
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
    pan: { x: 214.0625, y: 131.25 },
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

void test("shots rejects changes that cannot fit at the legibility floor", () => {
  assert.throws(() => {
    assertReadableFrame({
      ...frame,
      zoom: 11 / 16,
      focus: [{ ...changed, x2: 1500 }],
      cone: [],
    });
  }, /changes do not fit/);
});

void test("shots rejects an off-center camera even when every target is clear", () => {
  assert.throws(() => {
    assertReadableFrame({ ...frame, pan: { x: 100, y: 35 } });
  }, /center/);
});

void test("shots centers within graph bounds, including graphs smaller than the clear area", () => {
  assertReadableFrame({ ...frame, graph: [changed, near] });
  const origin = { id: "origin", x1: 0, y1: 0, x2: 50, y2: 50 };
  const bounded = {
    ...frame,
    graph: [{ ...origin, id: "graph", x2: 6000, y2: 6000 }],
    focus: [origin],
    cone: [],
    pan: { x: 80, y: 80 },
  };
  assertReadableFrame(bounded);
  assert.throws(() => {
    assertReadableFrame({ ...bounded, pan: { x: 275, y: 175 } });
  }, /center/);
});

void test("shots prefers the nearest uncut module view over a centered cut", () => {
  const caller = { id: "caller", x1: 500, y1: 400, x2: 700, y2: 450 };
  const modules = [
    { id: "one", x1: 400, y1: 300, x2: 800, y2: 800 },
    { id: "two", x1: 900, y1: 300, x2: 1200, y2: 800 },
  ];
  const uncut = {
    ...frame,
    size: { width: 680, height: 800 },
    focus: [caller],
    cone: [],
    modules,
    graph: [{ id: "graph", x1: 0, y1: 0, x2: 2400, y2: 2000 }],
    pan: { x: -220, y: -40 },
  };
  assertReadableFrame(uncut);
  assert.throws(() => {
    assertReadableFrame({ ...uncut, pan: { x: -260, y: -25 } });
  }, /uncut/);
  assert.throws(() => {
    assertReadableFrame({ ...uncut, pan: { x: -220, y: -50 } });
  }, /center/);
});

void test("shots marks a caller behind bottom overlays even inside the full viewport", () => {
  const below = { id: "bottom", x1: 150, x2: 200, y1: 290, y2: 330, distance: 2 };
  const far = { ...near, id: "far", x1: 5000, x2: 5050, distance: 2 };
  const bottom = {
    ...frame,
    area: { x1: 40, y1: 40, x2: 560, y2: 320 },
    pan: { x: 75, y: 15 },
    cone: [near, below, far],
    outOfView: [far.id],
  };
  assert.throws(() => {
    assertReadableFrame(bottom);
  }, /actual visibility/);
  assertReadableFrame({ ...bottom, outOfView: [below.id, far.id] });
});
