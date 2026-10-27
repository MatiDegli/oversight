import assert from "node:assert/strict";
import { test } from "node:test";

import { assertPanelFrame, assertReadableFrame } from "./framing.ts";

const changed = { id: "changed", x1: 100, y1: 80, x2: 150, y2: 120 };
const near = { id: "caller", x1: 250, y1: 150, x2: 350, y2: 250, distance: 1 };
const frame = {
  zoom: 1,
  pan: { x: 75, y: 35 },
  size: { width: 600, height: 400 },
  focus: [changed],
  cone: [near],
};

void test("live diff frames only the change despite lit callers, with no review card or rows", () => {
  const live = { ...frame, pan: { x: 175, y: 100 } };
  assertPanelFrame(1, live, "live-diff");
  assert.throws(() => {
    assertPanelFrame(1, { ...live, zoom: 0.8 }, "live-diff");
  }, /expected 1/);
  assert.throws(() => {
    assertPanelFrame(1, frame, "live-diff");
  }, /off center/);
  assert.throws(() => {
    assertPanelFrame(1, { ...live, card: { x1: 24, y1: 24, x2: 304, y2: 180 } }, "live-diff");
  }, /review card/);
  assert.throws(() => {
    assertPanelFrame(1, { ...live, outOfView: [near.id] }, "live-diff");
  }, /review rows/);
  assertPanelFrame(1, frame, "review");
  assert.throws(() => {
    assertPanelFrame(1, live, "review");
  }, /off center/);
});

void test("shots keeps watch zoom if the lit set fits, rejecting unnecessary shrink or wrong centering", () => {
  assertReadableFrame(1, frame);
  assert.throws(() => {
    assertReadableFrame(1, { ...frame, zoom: 0.9 });
  }, /expected 1/);
  assert.throws(() => {
    assertReadableFrame(1, { ...frame, pan: { x: 175, y: 35 } });
  }, /off center/);
});

void test("shots permits only the zoom needed to fit when it is above the floor", () => {
  const wider = { ...frame, size: { width: 300, height: 400 } };
  assertReadableFrame(1, { ...wider, zoom: 0.88, pan: { x: -48, y: 54.8 } });
  assert.throws(() => {
    assertReadableFrame(1, wider);
  }, /expected 0.88/);
  assert.throws(() => {
    assertReadableFrame(1, { ...wider, zoom: 0.8 });
  }, /expected 0.88/);
});

void test("shots rejects the old full-cone fit below 12px and maximizes distance counts at the floor", () => {
  const far = { id: "far", x1: 2000, y1: 80, x2: 2050, y2: 120, distance: 2 };
  const capped = {
    ...frame,
    zoom: 0.8,
    pan: { x: 200, y: 120 },
    cone: [far, near],
  };
  assertReadableFrame(1, capped);
  assert.throws(() => {
    assertReadableFrame(1, { ...capped, zoom: 0.675 });
  }, /labels below 12px/);
  assert.throws(() => {
    assertReadableFrame(1, { ...capped, pan: { x: 120, y: 68 } });
  }, /closer to centering/);
});

void test("shots still checks a live diff with no cone and a nearest ring too wide to fit", () => {
  const centeredChange = { ...frame, pan: { x: 175, y: 100 }, cone: [] };
  assertReadableFrame(1, centeredChange);
  assertReadableFrame(1, {
    ...centeredChange,
    zoom: 0.8,
    pan: { x: 200, y: 120 },
    cone: [{ ...near, x2: 2000 }],
  });
});

void test("shots checks callers beyond a skipped distance-1 node and marks from actual visibility", () => {
  const far = { id: "far", x1: 2000, y1: 80, x2: 2050, y2: 120, distance: 1 };
  const distance2 = { id: "distance2", x1: 400, y1: 80, x2: 450, y2: 120, distance: 2 };
  const capped = {
    ...frame,
    size: { width: 904, height: 400 },
    leftInset: 304,
    zoom: 0.8,
    pan: { x: 504, y: 120 },
    cone: [distance2, far, near],
    outOfView: ["far"],
  };
  assertReadableFrame(1, capped);
  assert.throws(() => {
    assertReadableFrame(1, { ...capped, pan: { x: 424, y: 68 } });
  }, /closer to centering/);
  assert.throws(() => {
    assertReadableFrame(1, { ...capped, pan: { x: 250, y: 68 } });
  }, /outside fit area/);
  assert.throws(() => {
    assertReadableFrame(1, { ...capped, outOfView: ["far", "distance2"] });
  }, /out-of-view card marks/);
});

void test("shots rejects the smaller distance-1 group and incorrect distance or centering ties", () => {
  const left = { id: "left", x1: -450, x2: -400, y1: 80, y2: 120, distance: 1 };
  const group = [600, 650, 700].map((x, i) => ({
    ...left,
    id: `right-${String(i)}`,
    x1: x,
    x2: x + 50,
  }));
  const capped = {
    ...frame,
    zoom: 0.8,
    pan: { x: -40, y: 120 },
    cone: [left, ...group],
    outOfView: ["left"],
  };
  assertReadableFrame(1, capped);
  assert.throws(() => {
    assertReadableFrame(1, { ...capped, pan: { x: 400, y: 120 } });
  }, /shows more callers/);
  const right = group[0];
  assert.ok(right);
  const second = { ...left, id: "second", distance: 2 };
  assert.throws(() => {
    assertReadableFrame(1, { ...capped, pan: { x: 40, y: 120 }, cone: [left, right, second] });
  }, /shows more callers/);
});

void test("shots does not mark whole visible boxes in fit padding, but marks partial boxes", () => {
  const anchor = { id: "anchor", x1: -200, x2: 450, y1: 80, y2: 120, distance: 1 };
  const padding = { id: "padding", x1: 470, x2: 490, y1: 80, y2: 120, distance: 2 };
  const clipped = { ...padding, id: "clipped", x1: 490, x2: 510 };
  const capped = {
    ...frame,
    size: { width: 904, height: 400 },
    leftInset: 304,
    zoom: 0.8,
    pan: { x: 504, y: 120 },
    cone: [anchor, padding, clipped],
    outOfView: ["clipped"],
  };
  assertReadableFrame(1, capped);
  assert.throws(() => {
    assertReadableFrame(1, { ...capped, outOfView: ["padding", "clipped"] });
  }, /out-of-view card marks/);
});

void test("shots keeps watch zoom when everything fits beside the measured card", () => {
  assertReadableFrame(1, {
    ...frame,
    size: { width: 904, height: 400 },
    leftInset: 304,
    pan: { x: 379, y: 35 },
    outOfView: [],
  });
});

void test("shots checks the measured card rectangle, including a visible caller below it", () => {
  const far = { id: "far", x1: 2000, x2: 2050, y1: 80, y2: 120, distance: 1 };
  const anchor = { id: "anchor", x1: -200, x2: 450, y1: 80, y2: 120, distance: 1 };
  const below = { id: "below", x1: -270, x2: -250, y1: 80, y2: 120, distance: 2 };
  const under = { ...below, id: "under", y1: 30, y2: 60 };
  const capped = {
    ...frame,
    size: { width: 904, height: 400 },
    leftInset: 304,
    card: { x1: 24, y1: 24, x2: 304, y2: 180 },
    zoom: 0.8,
    pan: { x: 504, y: 120 },
    cone: [far, anchor, below, under],
    outOfView: ["far", "under"],
  };
  assertReadableFrame(1, capped);
  for (const wrong of [["far"], ["far", "under", "below"]]) {
    assert.throws(() => {
      assertReadableFrame(1, { ...capped, outOfView: wrong });
    }, /out-of-view card marks/);
  }
});
