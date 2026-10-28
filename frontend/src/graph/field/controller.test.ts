import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { themes } from "../../theme";
import { createFieldController } from "./controller";
import type { FieldHost, FieldInput } from "./controller";
import type { BoxSource } from "./boxSource";
import { createLampRenderer } from "./renderer";
import { createLampField } from "./model";

vi.mock("./renderer", () => ({ createLampRenderer: vi.fn() }));
vi.mock("./model", async (original) => ({
  ...(await original<typeof import("./model")>()),
  createLampField: vi.fn(),
}));

let now = 0;
let nextFrame = 0;
const mutations: MutationCallback[] = [];
const frames = new Map<number, FrameRequestCallback>();
const compute = vi.fn(() => ({ cols: 1, rows: 1, data: new Uint8ClampedArray(4) }));
const renderer = {
  check: vi.fn(),
  resize: vi.fn(),
  draw: vi.fn(),
  pixel: vi.fn(),
  lost: vi.fn(() => false),
  dispose: vi.fn(),
};
const media = Object.assign(new EventTarget(), { matches: false });
let canvas: FieldHost;
const source = {
  boxes: () => new Map([["a", { x1: 0, y1: 0, x2: 10, y2: 10 }]]),
  subscribe: () => () => undefined,
} as unknown as BoxSource;
const light: FieldInput = {
  mode: "watch",
  lights: [{ id: "a", source: "agent-a", weight: 1, pulses: false }],
};

beforeEach(() => {
  vi.clearAllMocks();
  frames.clear();
  mutations.length = 0;
  now = 0;
  nextFrame = 0;
  media.matches = false;
  vi.mocked(createLampField).mockReturnValue(compute);
  vi.mocked(createLampRenderer).mockReturnValue(renderer);
  vi.spyOn(performance, "now").mockImplementation(() => now);
  vi.stubGlobal("matchMedia", () => media);
  vi.stubGlobal("window", Object.assign(new EventTarget(), { devicePixelRatio: 1 }));
  vi.stubGlobal(
    "document",
    Object.assign(new EventTarget(), {
      hidden: false,
      documentElement: { dataset: { theme: "dark" } },
    }),
  );
  vi.stubGlobal(
    "MutationObserver",
    class {
      constructor(callback: MutationCallback) {
        mutations.push(callback);
      }
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.set(++nextFrame, callback);
    return nextFrame;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  canvas = Object.assign(new EventTarget(), {
    clientWidth: 70,
    clientHeight: 70,
    hidden: false,
  }) as FieldHost;
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function advance(ms: number) {
  now = ms;
  const callbacks = [...frames.values()];
  frames.clear();
  for (const callback of callbacks) callback(now);
}
const last = () =>
  compute.mock.calls.at(-1) as unknown as Parameters<ReturnType<typeof createLampField>>;

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
    const stage = { closest: () => null, querySelectorAll: () => [] };
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
