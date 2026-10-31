import { describe, expect, it, vi } from "vitest";

import {
  advance,
  canvas,
  frames,
  last,
  light,
  media,
  mutations,
  nextFrame,
  renderer,
  setupControllerClock,
  source,
} from "./controller.support";

import { themes } from "../../theme";
import { createFieldController } from "./controller";

let now = 0;
setupControllerClock({
  get now() {
    return now;
  },
  set now(value) {
    now = value;
  },
});

describe("field fallback", () => {
  it("drops the renderer and its lamp cache on context loss, cancels frames and stays on plain ground", () => {
    const controller = createFieldController(canvas, source);
    controller.update({
      ...light,
      lights: light.lights.map((item) => ({ ...item, pulses: true })),
    });
    expect(canvas.oversightField?.animating()).toBe(true);
    canvas.dispatchEvent(new Event("webglcontextlost", { cancelable: true }));
    expect(renderer.check).toHaveBeenCalledOnce();
    expect(canvas.oversightField?.state()).toBe("plain");
    expect(canvas.oversightField?.animating()).toBe(false);
    expect(canvas.oversightField?.lamp(0, 0)).toBeNull();
    expect(canvas.hidden).toBe(true);
    expect(renderer.dispose).toHaveBeenCalledOnce();
    canvas.dispatchEvent(new Event("webglcontextrestored"));
    expect(renderer.check).toHaveBeenCalledTimes(2);
    expect(canvas.oversightField?.state()).toBe("plain");
    controller.dispose();
  });
});

it.each([0.75, 1, 1.5, 2, 3])("caps device pixel ratio %s at one on draw and resize", (ratio) => {
  window.devicePixelRatio = ratio;
  const controller = createFieldController(canvas, source);
  expect(renderer.resize).toHaveBeenLastCalledWith(70, 70, Math.min(ratio, 1));
  Object.assign(canvas, { clientWidth: 141, clientHeight: 93 });
  window.dispatchEvent(new Event("resize"));
  expect(renderer.resize).toHaveBeenLastCalledWith(141, 93, Math.min(ratio, 1));
  expect(renderer.check).not.toHaveBeenCalled();
  controller.dispose();
});

describe("field transitions", () => {
  it.each([
    ["watch", "review"],
    ["watch", "file"],
    ["live-diff", "review"],
    ["live-diff", "file"],
  ] as const)(
    "stops breathing and rings immediately from %s to %s, including fading lights",
    (from, mode) => {
      const controller = createFieldController(canvas, source);
      controller.update({
        mode: from,
        lights: light.lights.map((item) => ({ ...item, pulses: true })),
      });
      advance(240);
      expect(last()[3][0]?.pulses).toBe(true);
      controller.update({
        mode,
        lights: [
          {
            id: "a",
            source: mode === "review" ? "agent-a" : "work-light",
            weight: 0.8,
            pulses: false,
          },
        ],
      });
      for (const time of [240, 360, 479]) {
        advance(time);
        const lights = last()[3];
        expect(lights).toHaveLength(mode === "review" ? 1 : 2);
        expect(lights.every((item) => !item.pulses && item.pulseWeight === 0)).toBe(true);
      }
      advance(480);
      expect(last()[3]).toHaveLength(1);
      expect(canvas.oversightField?.animating()).toBe(false);
      controller.dispose();
    },
  );

  it("eases light weights and mode profiles for the 240 ms token, then stops frames", () => {
    const controller = createFieldController(canvas, source);
    controller.update(light);
    expect(last()[3][0]?.weight ?? 0).toBe(0);
    expect(canvas.oversightField?.animating()).toBe(true);
    advance(120);
    expect(last()[3][0]?.weight).toBeGreaterThan(0.5);
    expect(last()[3][0]?.weight).toBeLessThan(1);
    advance(240);
    expect(last()[3][0]?.weight).toBe(1);
    expect(canvas.oversightField?.animating()).toBe(false);
    controller.update({ ...light, mode: "review" });
    expect(last()[0].profile.gain).toBe(0.92);
    advance(360);
    expect(last()[0].profile.gain).toBeGreaterThan(0.7);
    expect(last()[0].profile.gain).toBeLessThan(0.92);
    advance(480);
    expect(last()[0].profile.gain).toBe(0.7);
    expect(last()[0].profile.near).toBe(26);
    expect(canvas.oversightField?.animating()).toBe(false);
    controller.dispose();
  });

  it("fades removed lights out and starts a replacement from the displayed weight", () => {
    const controller = createFieldController(canvas, source);
    controller.update(light);
    advance(240);
    controller.update({ ...light, lights: [] });
    advance(360);
    const weight = last()[3][0]?.weight;
    expect(weight).toBeGreaterThan(0);
    expect(weight).toBeLessThan(0.5);
    controller.update(light);
    expect(last()[3][0]?.weight).toBeCloseTo(weight ?? -1);
    advance(600);
    expect(last()[3][0]?.weight).toBe(1);
    controller.update({ ...light, lights: [] });
    advance(840);
    expect(last()[3]).toEqual([]);
    controller.dispose();
  });

  it.each(["update", "frame"])(
    "paints the final empty scene when a slow %s draw crosses the transition deadline",
    (phase) => {
      const controller = createFieldController(canvas, source);
      controller.update(light);
      advance(240);
      const crossDeadline = () => {
        now = 481;
      };
      if (phase === "update") renderer.draw.mockImplementationOnce(crossDeadline);
      controller.update({ ...light, lights: [] });
      if (phase === "frame") {
        renderer.draw.mockImplementationOnce(crossDeadline);
        advance(479);
      }
      expect(last()[3]).toHaveLength(1);
      expect(canvas.oversightField?.animating()).toBe(true);
      advance(496);
      expect(last()[3]).toEqual([]);
      expect(canvas.oversightField?.animating()).toBe(false);
      expect(frames.size).toBe(0);
      controller.dispose();
    },
  );

  it("jumps immediately under reduced motion, including when it is enabled mid-transition", () => {
    const controller = createFieldController(canvas, source);
    controller.update(light);
    advance(120);
    media.matches = true;
    media.dispatchEvent(new Event("change"));
    expect(last()[3][0]?.weight).toBe(1);
    expect(canvas.oversightField?.animating()).toBe(false);
    controller.update({ ...light, mode: "review" });
    expect(last()[0].profile.gain).toBe(0.7);
    expect(last()[0].profile.near).toBe(26);
    expect(canvas.oversightField?.animating()).toBe(false);
    expect(themes.dark["transition-duration"]).toBe("240ms");
    controller.dispose();
  });
});

it("R4 starts the ring for working input and stops it immediately, while approval fades its marker light", () => {
  const signal = {
    id: "a",
    source: "agent-a",
    weight: 1,
    pulses: true,
    marker: "agent-1",
    steady: true,
  } as const;
  const controller = createFieldController(canvas, source);
  controller.update({ mode: "watch", lights: [signal] });
  advance(240);
  expect(last()[3][0]?.pulses).toBe(true);
  controller.update({ mode: "watch", lights: [{ ...signal, pulses: false }] });
  expect(last()[3][0]?.pulses).toBe(false);
  advance(480);
  controller.update({ mode: "watch", lights: [] });
  advance(600);
  expect(last()[3][0]?.weight).toBeGreaterThan(0);
  expect(last()[3][0]?.weight).toBeLessThan(1);
  advance(720);
  expect(last()[3]).toEqual([]);
  expect(canvas.oversightField?.animating()).toBe(false);
  controller.dispose();
});

it.each(["width", "height"])(
  "finishes a pending transition with zero %s instead of spinning frames",
  (axis) => {
    const controller = createFieldController(canvas, source);
    controller.update(light);
    const draws = renderer.draw.mock.calls.length;
    Object.assign(canvas, axis === "width" ? { clientWidth: 0 } : { clientHeight: 0 });
    advance(120);
    advance(240);
    const scheduled = nextFrame;
    for (let time = 256; time <= 400; time += 16) advance(time);
    expect(nextFrame).toBe(scheduled);
    expect(frames.size).toBe(0);
    expect(canvas.oversightField?.animating()).toBe(false);
    expect(renderer.draw).toHaveBeenCalledTimes(draws);
    Object.assign(canvas, { clientWidth: 70, clientHeight: 70 });
    window.dispatchEvent(new Event("resize"));
    expect(last()[3][0]?.weight).toBe(1);
    expect(renderer.draw).toHaveBeenCalledTimes(draws + 1);
    controller.dispose();
  },
);

it.each(["width", "height"])(
  "sleeps with pulsing lights at zero %s and wakes on redraw",
  (axis) => {
    const stage = {
      closest: () => null,
      querySelectorAll: () => [],
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    };
    Object.assign(canvas, {
      closest: () => stage,
      getBoundingClientRect: () => ({ left: 0, top: 0 }),
      ownerDocument: { querySelectorAll: () => [] },
    });
    const controller = createFieldController(canvas, source);
    controller.update({
      ...light,
      lights: light.lights.map((item) => ({ ...item, pulses: true })),
    });
    advance(240);
    expect(frames.size).toBe(1);
    const draws = renderer.draw.mock.calls.length;
    Object.assign(canvas, axis === "width" ? { clientWidth: 0 } : { clientHeight: 0 });
    advance(256);
    expect(frames.size).toBe(0);
    expect(canvas.oversightField?.animating()).toBe(false);
    expect(renderer.draw).toHaveBeenCalledTimes(draws);
    Object.assign(canvas, { clientWidth: 70, clientHeight: 70 });
    mutations[0]?.([{ target: stage } as unknown as MutationRecord], {} as MutationObserver);
    expect(renderer.draw).toHaveBeenCalledTimes(draws + 1);
    expect(frames.size).toBe(1);
    advance(272);
    expect(renderer.draw).toHaveBeenCalledTimes(draws + 2);
    Object.assign(canvas, { clientWidth: 0 });
    window.dispatchEvent(new Event("resize"));
    expect(frames.size).toBe(0);
    Object.assign(canvas, { clientWidth: 70 });
    window.dispatchEvent(new Event("resize"));
    expect(frames.size).toBe(1);
    controller.dispose();
  },
);

it("measures the overlays again when the layout moves with no change to the markup", async () => {
  // A font arrives, or a panel finishes growing: the overlay's box changes, the DOM does not.
  const listeners = new Map<string, () => void>();
  const stage = {
    closest: () => null,
    querySelectorAll: () => [overlay],
    addEventListener: (type: string, listener: () => void) => listeners.set(type, listener),
    removeEventListener: (type: string) => listeners.delete(type),
  };
  let right = 20;
  const overlay = {
    parentElement: null,
    getBoundingClientRect: () => ({ left: 0, top: 0, right, bottom: 10, width: right, height: 10 }),
  };
  const fonts = Promise.resolve();
  vi.stubGlobal("document", Object.assign(document, { fonts: { ready: fonts } }));
  Object.assign(canvas, {
    closest: () => stage,
    getBoundingClientRect: () => ({ left: 0, top: 0 }),
    ownerDocument: { querySelectorAll: () => [] },
  });
  const controller = createFieldController(canvas, source);
  const masked = () => renderer.draw.mock.calls.at(-1)?.[2] as { x2: number }[];
  await fonts;
  expect(masked()).toEqual([{ x1: 0, y1: 0, x2: 20, y2: 10 }]);
  right = 40;
  listeners.get("transitionend")?.();
  expect(masked()).toEqual([{ x1: 0, y1: 0, x2: 40, y2: 10 }]);
  controller.dispose();
  expect(listeners.has("transitionend")).toBe(false);
});
