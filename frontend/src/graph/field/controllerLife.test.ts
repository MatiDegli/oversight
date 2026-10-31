import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  advance,
  canvas,
  compute,
  last,
  light,
  media,
  renderer,
  setupControllerClock,
  settle,
  source,
} from "./controller.support";

import { themes } from "../../theme";
import { createFieldController } from "./controller";
import type { FieldInput } from "./controller";
import type { BoxSource } from "./boxSource";
import type { Contours } from "./terrain";
import { createLampField } from "./model";
import { MarkerLayer } from "./markerLayer";
import { TravelLayer } from "./travel";

let now = 0;
setupControllerClock({
  get now() {
    return now;
  },
  set now(value) {
    now = value;
  },
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

  it.each(["agent-a", "agent-b"] as const)(
    "lights the travelling head's LEDs in %s, under the drawn masks",
    async (team) => {
      const real = await vi.importActual<typeof import("./model")>("./model");
      vi.mocked(createLampField).mockReturnValue(real.createLampField());
      Object.assign(canvas, { clientWidth: 700, clientHeight: 300 });
      let headers: { x1: number; y1: number; x2: number; y2: number }[] = [];
      const wide = {
        ...source,
        boxes: () => new Map([["a", { x1: 50, y1: 120, x2: 60, y2: 140 }]]),
        headers: () => headers,
      } as unknown as BoxSource;
      const controller = createFieldController(canvas, wide);
      controller.update({ mode: "watch", lights: [], merges: [] });
      controller.update({ ...live, merges: [{ ...merge, source: team }] });
      advance(look.delay + look.travel / 2);
      // The path runs from x=700 to x=60: eased halfway is x=140, far from the target's light.
      const lamp = canvas.oversightField?.lamp(143.5, 129.5);
      expect(lamp?.level).toBeGreaterThan(0);
      const color = lamp?.color ?? [0, 0, 0];
      if (team === "agent-a") expect(color[0]).toBeGreaterThan(color[2]);
      else expect(color[2]).toBeGreaterThan(color[0]);
      headers = [{ x1: 133, y1: 119, x2: 154, y2: 140 }];
      controller.redraw();
      expect(canvas.oversightField?.lamp(143.5, 129.5)?.level).toBe(0);
      expect(renderer.draw.mock.calls.at(-1)?.[2]).toEqual(headers);
      // Switching motion off mid-flight clears the emitter and the SVG immediately.
      media.matches = true;
      media.dispatchEvent(new Event("change"));
      expect(canvas.oversightField?.life().pulses).toBe(0);
      headers = [];
      controller.redraw();
      expect(canvas.oversightField?.lamp(143.5, 129.5)?.level).toBe(0);
      controller.dispose();
    },
  );

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

describe("frame headers", () => {
  it("hide the lamps behind them at every draw, following the camera without a graph event", () => {
    let header = { x1: 0, y1: 0, x2: 70, y2: 20 };
    const framed = {
      ...source,
      headers: () => [header],
    } as unknown as BoxSource;
    compute.mockReturnValue({ cols: 10, rows: 10, data: new Uint8ClampedArray(400).fill(255) });
    const controller = createFieldController(canvas, framed);
    controller.update(light);
    const drawn = () => renderer.draw.mock.calls.at(-1)?.[2] as { y2: number }[];
    expect(drawn()).toContainEqual(header);
    expect(canvas.oversightField?.masks()).toContainEqual(header);
    // The frame moves with the camera: the next draw masks where it is now, not where it was.
    header = { x1: 0, y1: 30, x2: 70, y2: 50 };
    controller.redraw();
    expect(drawn()).toContainEqual(header);
    expect(drawn().some((box) => box.y2 === 20)).toBe(false);
    expect(canvas.oversightField?.masks()).toEqual(drawn());
    // The lamp under a header is dark; the lamp in the frame's body is the grid's.
    expect(canvas.oversightField?.lamp(10, 40)?.level).toBe(0);
    expect(canvas.oversightField?.lamp(10, 10)?.level).toBe(3);
    controller.dispose();
  });

  it("hide the lamps and lines only: a marker or a pulse over a header or a folded card is not clipped", () => {
    const header = { x1: 0, y1: 0, x2: 70, y2: 20 };
    const framed = { ...source, headers: () => [header] } as unknown as BoxSource;
    const marker = vi.spyOn(MarkerLayer.prototype, "draw");
    const travel = vi.spyOn(TravelLayer.prototype, "draw");
    const controller = createFieldController(canvas, framed);
    controller.update({
      mode: "watch",
      lights: [{ id: "a", source: "agent-a", marker: "agent-1", weight: 1, pulses: false }],
    });
    // The lamps are hidden under the header...
    expect(renderer.draw.mock.calls.at(-1)?.[2]).toContainEqual(header);
    // ...but the letter above the graph and the pulse on its way keep only the overlays' masks.
    expect(marker).toHaveBeenCalled();
    expect(travel).toHaveBeenCalled();
    expect(marker.mock.calls.at(-1)?.[2]).not.toContainEqual(header);
    expect(travel.mock.calls.at(-1)?.[2]).not.toContainEqual(header);
    controller.dispose();
  });

  it("hide nothing when the source has no headers", () => {
    const controller = createFieldController(canvas, source);
    controller.update(light);
    expect(canvas.oversightField?.masks()).toEqual([]);
    controller.dispose();
  });
});
