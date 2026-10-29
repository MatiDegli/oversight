import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { themes } from "../../theme";
import { createFieldController } from "./controller";
import type { FieldHost, FieldInput } from "./controller";
import type { BoxSource } from "./boxSource";
import type { Contours } from "./terrain";
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
/** Runs the frames the field asked for until it asks for no more, within a bound. */
function settle() {
  for (let i = 0; i < 5 && frames.size > 0; i++) advance(now + 300);
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

describe("the rest ground", () => {
  const frames = new Map([
    ["shop.cart", { x1: 100, y1: 300, x2: 360, y2: 480 }],
    ["shop.pricing", { x1: 700, y1: 150, x2: 960, y2: 330 }],
  ]);
  const withFrames = {
    ...source,
    frames: (ids: readonly string[]) =>
      new Map(ids.flatMap((id) => (frames.has(id) ? [[id, frames.get(id)] as const] : []))),
  } as unknown as BoxSource;
  const modules = [
    { id: "shop.cart", callers: 1 },
    { id: "shop.pricing", callers: 9 },
  ];
  const ground: FieldInput = { mode: "watch", lights: [], modules };
  const contoursOf = (): Contours | undefined =>
    renderer.draw.mock.calls.at(-1)?.[3] as Contours | undefined;
  const big = () => Object.assign(canvas, { clientWidth: 1200, clientHeight: 700 });

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  });

  it("is drawn only for a field told its modules, over their frames, with the line's look", () => {
    big();
    const controller = createFieldController(canvas, withFrames);
    controller.update({ mode: "watch", lights: [] });
    expect(contoursOf()).toBeUndefined();
    expect(canvas.oversightField?.life().contours).toBe(false);
    controller.update(ground);
    const contours = contoursOf();
    expect(contours?.look).toMatchObject({ step: 0.1, levels: 8, fade: 0.65, width: 1 });
    expect(contours?.look.color).toEqual([237, 237, 234]);
    // 12 by 7 cells of 100px, plus an edge: the height peaks over a module's frame.
    expect(contours?.heights.cell).toBe(16);
    const at = (x: number, y: number) =>
      contours?.heights.data[
        (Math.round(y / 16) * contours.heights.cols + Math.round(x / 16)) * 2
      ] ?? 0;
    expect(at(830, 240)).toBeGreaterThan(0.5);
    expect(at(230, 390)).toBeGreaterThan(0.45);
    expect(at(830, 240)).toBeGreaterThan(at(230, 390));
    expect(canvas.oversightField?.life().contours).toBe(true);
    controller.dispose();
  });

  it("keeps its heights for the slow interval, then drifts to new ones, and repaints on a timer", () => {
    big();
    const controller = createFieldController(canvas, withFrames);
    controller.update(ground);
    settle();
    expect(canvas.oversightField?.animating()).toBe(false);
    const first = contoursOf()?.heights.data.slice();
    const draws = canvas.oversightField?.draws() ?? 0;
    now += 50;
    controller.redraw();
    expect(contoursOf()?.heights.data).toEqual(first);
    // The slow timer, with no frame running, repaints at the interval: heights move on.
    now = 120_000;
    vi.advanceTimersByTime(500);
    expect(canvas.oversightField?.draws()).toBe(draws + 2);
    expect(contoursOf()?.heights.data).not.toEqual(first);
    controller.dispose();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("stands still under reduced motion: no clock, no timer, the same ground at any time", () => {
    media.matches = true;
    big();
    const controller = createFieldController(canvas, withFrames);
    controller.update(ground);
    const first = contoursOf()?.heights.data.slice();
    expect(first?.some((value) => value > 0.3)).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    now = 300_000;
    controller.redraw();
    expect(contoursOf()?.heights.data).toEqual(first);
    expect(vi.getTimerCount()).toBe(0);
    controller.dispose();
  });

  it("follows the frames: moving a module moves its hill", () => {
    big();
    const controller = createFieldController(canvas, withFrames);
    controller.update(ground);
    const before = contoursOf()?.heights.data.slice();
    frames.set("shop.pricing", { x1: 300, y1: 450, x2: 560, y2: 630 });
    controller.redraw();
    expect(contoursOf()?.heights.data).not.toEqual(before);
    frames.set("shop.pricing", { x1: 700, y1: 150, x2: 960, y2: 330 });
    controller.dispose();
  });

  it("covers the contours where the lamps' glow is, and leaves them alone elsewhere", () => {
    big();
    compute.mockReturnValue({
      cols: 172,
      rows: 100,
      data: new Uint8ClampedArray(172 * 100 * 4),
      glow: new Uint8ClampedArray(172 * 100).fill(255),
    } as never);
    const controller = createFieldController(canvas, withFrames);
    controller.update(ground);
    const cover = (): number[] =>
      Array.from(contoursOf()?.heights.data ?? []).filter((_, i) => i % 2 === 1);
    expect(cover().every((value) => value === 1)).toBe(true);
    compute.mockReturnValue({ cols: 1, rows: 1, data: new Uint8ClampedArray(4) });
    controller.redraw();
    expect(cover().every((value) => value === 0)).toBe(true);
    controller.dispose();
  });
});

describe("a merge's pulse and light", () => {
  const merge = {
    key: "change-1:21",
    slot: "A" as const,
    source: "agent-a" as const,
    id: "a",
    pulse: true,
    age: 0,
  };
  const live: FieldInput = { mode: "watch", lights: [], merges: [merge] };
  /** The change's light just before its decision: waiting on an open entry. */
  const waiting: FieldInput = {
    mode: "watch",
    lights: [
      {
        id: "a",
        source: "agent-a",
        weight: themes.dark["waiting-light-weight"],
        pulses: false,
        steady: true,
      },
    ],
    merges: [],
  };
  const look = {
    delay: Number.parseFloat(themes.dark["merge-travel-delay"]),
    travel: Number.parseFloat(themes.dark["merge-travel-duration"]),
    fade: Number.parseFloat(themes.dark["afterlight-duration"]),
  };
  const arrival = look.delay + look.travel;
  const burning = () => {
    const placed = last()[3];
    return placed.find((item) => item.steady === true && !item.pulses);
  };

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    Object.assign(canvas, { getBoundingClientRect: () => ({ left: 0, top: 0 }) });
    Object.assign(document, { querySelector: () => null });
  });

  it("treats the merges the field mounts on as history: no pulse, no light, no timer", () => {
    const controller = createFieldController(canvas, source);
    controller.update(live);
    settle();
    expect(canvas.oversightField?.life()).toMatchObject({ pulses: 0, lights: 0 });
    expect(canvas.oversightField?.animating()).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
    controller.dispose();
  });

  it("sends a pulse for a merge that arrives later, then lights the node and fades it", () => {
    const controller = createFieldController(canvas, source);
    controller.update(waiting);
    now = 1000;
    controller.update(live);
    expect(canvas.oversightField?.life().pulses).toBe(0);
    now = 1000 + look.delay + look.travel / 2;
    controller.redraw();
    expect(canvas.oversightField?.life().pulses).toBe(1);
    expect(canvas.oversightField?.animating()).toBe(true);
    // The light holds the waiting level while the pulse travels, and never rises above it.
    expect(burning()?.weight).toBeCloseTo(themes.dark["waiting-light-weight"], 10);
    now = 1000 + arrival + look.fade / 2;
    controller.redraw();
    settle();
    const half = burning();
    expect(half?.weight).toBeGreaterThan(0);
    expect(half?.weight).toBeLessThan(themes.dark["waiting-light-weight"] * 0.5 ** 1.6 + 1e-9);
    expect(canvas.oversightField?.life()).toMatchObject({ pulses: 0, lights: 1 });
    expect(canvas.oversightField?.animating()).toBe(false);
    expect(vi.getTimerCount()).toBe(1);
    now = 1000 + arrival + look.fade;
    vi.advanceTimersByTime(500);
    expect(canvas.oversightField?.life().lights).toBe(0);
    expect(burning()).toBeUndefined();
    expect(vi.getTimerCount()).toBe(0);
    controller.dispose();
  });

  it("fades the light on the slow timer, with no animation frame, and weaker each time", () => {
    const controller = createFieldController(canvas, source);
    controller.update(waiting);
    controller.update(live);
    const weights: number[] = [];
    for (const share of [0.1, 0.3, 0.6, 0.9]) {
      now = arrival + look.fade * share;
      vi.advanceTimersByTime(500);
      settle();
      weights.push(burning()?.weight ?? 0);
    }
    expect(weights.every((weight) => weight > 0)).toBe(true);
    expect(weights).toEqual([...weights].sort((a, b) => b - a));
    expect(canvas.oversightField?.animating()).toBe(false);
    controller.dispose();
  });

  it("has no pulse under reduced motion: the light is whole at once and fades as long", () => {
    media.matches = true;
    const controller = createFieldController(canvas, source);
    controller.update(waiting);
    now = 5;
    controller.update(live);
    expect(canvas.oversightField?.life()).toMatchObject({ pulses: 0, lights: 1 });
    expect(canvas.oversightField?.animating()).toBe(false);
    const whole = burning()?.weight ?? 0;
    expect(whole).toBeCloseTo(themes.dark["waiting-light-weight"], 10);
    now = 5 + look.fade / 2;
    vi.advanceTimersByTime(500);
    expect(burning()?.weight).toBeLessThan(whole / 2);
    now = 5 + look.fade;
    vi.advanceTimersByTime(500);
    expect(burning()).toBeUndefined();
    expect(vi.getTimerCount()).toBe(0);
    controller.dispose();
  });

  it("does not start a merge it has seen again, and starts it again after it leaves the state", () => {
    const controller = createFieldController(canvas, source);
    controller.update({ mode: "watch", lights: [], merges: [] });
    controller.update(live);
    now = look.delay + 100;
    controller.update({ ...live, merges: [{ ...merge, age: 5000 }] });
    expect(canvas.oversightField?.life().pulses).toBe(1);
    now = arrival + look.fade + 1;
    vi.advanceTimersByTime(500);
    expect(canvas.oversightField?.life().lights).toBe(0);
    controller.update({ mode: "watch", lights: [], merges: [] });
    controller.update(live);
    expect(canvas.oversightField?.life().pulses).toBe(0);
    now += look.delay + 100;
    controller.redraw();
    expect(canvas.oversightField?.life().pulses).toBe(1);
    controller.dispose();
  });

  it("lights nothing for a rejection whose change had no light, and fades the light of one that had", () => {
    const rejection = { ...live, merges: [{ ...merge, pulse: false }] };
    const dark = createFieldController(canvas, source);
    dark.update({ mode: "watch", lights: [], merges: [] });
    now = 10;
    dark.update(rejection);
    settle();
    expect(canvas.oversightField?.life()).toMatchObject({ pulses: 0, lights: 0 });
    expect(canvas.oversightField?.animating()).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
    dark.dispose();
    const lit = createFieldController(canvas, source);
    lit.update(waiting);
    now = 20;
    lit.update(rejection);
    expect(canvas.oversightField?.life()).toMatchObject({ pulses: 0, lights: 1 });
    // It comes up as the waiting light goes out, to the same level, and fades from there.
    now = 20 + Number.parseFloat(themes.dark["transition-duration"]);
    lit.redraw();
    expect(burning()?.weight).toBeCloseTo(themes.dark["waiting-light-weight"], 2);
    lit.dispose();
  });

  it("shows an approve whose change had no light the pulse first, and the light as it arrives", () => {
    const controller = createFieldController(canvas, source);
    controller.update({ mode: "watch", lights: [], merges: [] });
    now = 1000;
    controller.update(live);
    now = 1000 + look.delay + look.travel / 2;
    controller.redraw();
    expect(canvas.oversightField?.life()).toMatchObject({ pulses: 1, lights: 0 });
    expect(burning()).toBeUndefined();
    now = 1000 + arrival + 300;
    controller.redraw();
    expect(canvas.oversightField?.life().lights).toBe(1);
    expect(burning()?.weight).toBeGreaterThan(0);
    controller.dispose();
  });
});
