import { expect, it } from "vitest";
import { themes } from "../../theme";
import { fieldConfig, signalConfig } from "./model";
import { FieldTransition, ease } from "./transition";
import {
  advance,
  canvas,
  last,
  light,
  media,
  setupControllerClock,
  source,
} from "./controller.support";
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

it.each(["dark", "day"] as const)("consumes fixed map tokens at every zoom in %s", (theme) => {
  for (const zoom of [0.21, 0.3, 0.6])
    for (const mode of ["watch", "live-diff", "file", "review"] as const) {
      const config = fieldConfig(themes[theme], mode, "map");
      expect(signalConfig(config, zoom).profile).toEqual({ near: 30, far: 84, gain: 0.85 });
      const custom = fieldConfig(
        {
          ...themes[theme],
          "map-light-near": "37px",
          "map-light-far": "93px",
          "map-light-gain": 0.73,
        },
        mode,
        "map",
      );
      expect(signalConfig(custom, zoom).profile).toEqual({ near: 37, far: 93, gain: 0.73 });
      // Declaring map tokens (or opting into the layer) does not publish the map level.
      const cards = fieldConfig(themes[theme], mode);
      expect(signalConfig(cards, zoom).profile).toEqual({
        near: 26 * zoom,
        far: 72 * zoom,
        gain: themes[theme][
          mode === "file" ? "file-view-light-gain" : (`${mode}-light-gain` as const)
        ],
      });
    }
});
it.each(["map", "cards"] as const)(
  "interpolates effective screen profiles toward %s and on interruption",
  (level) => {
    const theme = themes.dark,
      zoom = level === "map" ? 0.21 : 0.7;
    const from = fieldConfig(theme, "watch", level === "map" ? "cards" : "map");
    const to = fieldConfig(theme, "watch", level);
    const transition = new FieldTransition({ lights: light.lights, config: from });
    const set = (config: typeof from, at: number, jump = false) => {
      transition.set(light, config, at, "240ms", theme["transition-easing"], jump, zoom);
    };
    const before = transition.sample(0, zoom);
    set(to, 0);
    expect(transition.sample(0, zoom).config.profile).toEqual(before.config.profile);
    const middle = transition.sample(120, zoom);
    const a = signalConfig(from, zoom).profile,
      b = signalConfig(to, zoom).profile;
    const t = ease(0.5, theme["transition-easing"]);
    for (const key of ["near", "far", "gain"] as const)
      expect(middle.config.profile[key]).toBeCloseTo(a[key] + (b[key] - a[key]) * t);
    expect(middle.lights[0]?.weight).toBe(1);
    set(from, 120);
    expect(transition.sample(120, zoom)).toEqual(middle);
    expect(transition.sample(360, zoom).config.profile).toEqual(a);
    set(to, 400, true);
    expect(transition.sample(400, zoom).config.profile).toEqual(b);
    expect(transition.active(400)).toBe(false);
  },
);
it("keeps card-only mode transitions scaled with the moving camera", () => {
  const theme = themes.dark;
  const transition = new FieldTransition({
    lights: light.lights,
    config: fieldConfig(theme, "watch"),
  });
  transition.set(
    { ...light, mode: "review" },
    fieldConfig(theme, "review"),
    0,
    "240ms",
    theme["transition-easing"],
    false,
    0.3,
  );
  for (const [at, zoom] of [
    [0, 0.3],
    [120, 0.5],
    [240, 0.8],
  ]) {
    const sampled = transition.sample(at ?? 0, zoom);
    expect(sampled.config.profile.near).toBeCloseTo(26 * (zoom ?? 1));
    expect(sampled.config.profile.far).toBeCloseTo(72 * (zoom ?? 1));
  }
});
it("retargets on the published level alone, holds lights through explicit moves, and cleans up", () => {
  let zoom = 0.6,
    level: "map" | "cards" = "cards";
  let notify: () => void = () => undefined;
  let unsubscribed = false;
  const controller = createFieldController(canvas, {
    ...source,
    zoom: () => zoom,
    level: () => level,
    subscribe(listener) {
      notify = listener;
      return () => {
        unsubscribed = true;
      };
    },
  });
  controller.update(light);
  advance(240);
  expect(last()[0].profile.near).toBeCloseTo(15.6);
  const before = last()[0].profile;
  // Explicit camera intent publishes map while its camera is still at card zoom.
  level = "map";
  notify();
  expect(last()[0].profile).toEqual(before);
  expect(last()[3][0]?.weight).toBe(1);
  zoom = 0.21;
  advance(300);
  advance(360);
  expect(last()[0].profile.near).toBeGreaterThan(15.6);
  expect(last()[0].profile.near).toBeLessThan(30);
  const middle = last()[0].profile;
  level = "cards";
  notify();
  expect(last()[0].profile).toEqual(middle);
  zoom = 0.8;
  advance(480);
  advance(600);
  expect(last()[0].profile).toEqual({ near: 20.8, far: 57.6, gain: 0.92 });
  level = "map";
  notify();
  advance(720);
  advance(840);
  expect(last()[0].profile).toEqual({ near: 30, far: 84, gain: 0.85 });
  for (zoom of [0.21, 0.3, 0.6]) {
    notify();
    expect(last()[0].profile).toEqual({ near: 30, far: 84, gain: 0.85 });
    expect(last()[3][0]?.weight).toBe(1);
  }
  controller.dispose();
  expect(unsubscribed).toBe(true);
});
it("jumps on reduced motion in both directions and when enabled during a crossing", () => {
  let level: "map" | "cards" = "cards";
  let notify: () => void = () => undefined;
  const controller = createFieldController(canvas, {
    ...source,
    zoom: () => 0.3,
    level: () => level,
    subscribe(listener) {
      notify = listener;
      return () => undefined;
    },
  });
  controller.update(light);
  advance(240);
  level = "map";
  notify();
  advance(360);
  media.matches = true;
  media.dispatchEvent(new Event("change"));
  expect(last()[0].profile).toEqual({ near: 30, far: 84, gain: 0.85 });
  expect(canvas.oversightField?.animating()).toBe(false);
  level = "cards";
  notify();
  expect(last()[0].profile).toEqual({ near: 7.8, far: 21.599999999999998, gain: 0.92 });
  level = "map";
  notify();
  expect(last()[0].profile).toEqual({ near: 30, far: 84, gain: 0.85 });
  expect(last()[3][0]?.weight).toBe(1);
  controller.dispose();
});
it("keeps card spreads on a small zoom without a published map level", () => {
  media.matches = true;
  const controller = createFieldController(canvas, { ...source, zoom: () => 0.21 });
  controller.update(light);
  expect(last()[0].profile).toEqual({ near: 26 * 0.21, far: 72 * 0.21, gain: 0.92 });
  controller.dispose();
});
