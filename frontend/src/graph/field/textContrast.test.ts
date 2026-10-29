import { describe, expect, it } from "vitest";

import { themes } from "../../theme";
import type { Rgb } from "./model";
import { createLampField, fieldConfig, parseHex } from "./model";
import { sketchBusy } from "./sketchScene.fixture";

type Name = "dark" | "day";
type Fill = { color: string; alpha: number };

/** WCAG relative luminance of an sRGB color. */
function luminance([r, g, b]: Rgb): number {
  const channel = (value: number) => {
    const unit = value / 255;
    return unit <= 0.04045 ? unit / 12.92 : ((unit + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

const contrast = (a: Rgb, b: Rgb) => {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return ((high ?? 0) + 0.05) / ((low ?? 0) + 0.05);
};

/** A fill of the given alpha laid over what is beneath it, as a browser composites in sRGB. */
const over = (fill: Rgb, alpha: number, beneath: Rgb): Rgb => [
  fill[0] * alpha + beneath[0] * (1 - alpha),
  fill[1] * alpha + beneath[1] * (1 - alpha),
  fill[2] * alpha + beneath[2] * (1 - alpha),
];

/**
 * The lamp the theme draws that sits worst under text: on dark ground the brightest lit lamp, on
 * paper the darkest. Both agents' lights run on the sketch's busy scene, so the whole ramp, up to
 * the hot color toward the ink, is drawn; the lamp is opaque where it is drawn.
 */
function worstLamp(name: Name, family: "agent-a" | "agent-b"): Rgb {
  const theme = themes[name];
  const lights = sketchBusy.lights
    .filter((light) => light.source === family)
    .map((light) => ({
      box: light.box,
      color: parseHex(theme[family]),
      weight: light.weight,
      pulses: false,
    }));
  const grid = createLampField()(
    fieldConfig(theme, "watch"),
    sketchBusy.width,
    sketchBusy.height,
    lights,
    null,
  );
  let worst: Rgb | null = null;
  for (let at = 0; at < grid.data.length; at += 4) {
    if ((grid.data[at + 3] ?? 0) === 0) continue;
    const lamp: Rgb = [grid.data[at] ?? 0, grid.data[at + 1] ?? 0, grid.data[at + 2] ?? 0];
    const better =
      worst === null ||
      (name === "dark" ? luminance(lamp) > luminance(worst) : luminance(lamp) < luminance(worst));
    if (better) worst = lamp;
  }
  if (!worst) throw new Error("No lamp lit");
  return worst;
}

/** Every text on a lit row or frame, over its fill and the worst lamp beneath, per theme and agent. */
function textContrasts(name: Name): Record<string, number> {
  const theme = themes[name];
  const found: Record<string, number> = {};
  for (const family of ["agent-a", "agent-b"] as const) {
    const lamp = worstLamp(name, family);
    const card = over(parseHex(theme.card), theme["node-opacity"], lamp);
    const fill = theme[`${family}-node`] as Fill;
    const editing = over(parseHex(fill.color), fill.alpha, lamp);
    const frame = over(parseHex(theme.panel), theme["module-opacity"], lamp);
    const words = parseHex(theme[`${family}-text`]);
    found[`${family} row name over a lit row`] = contrast(parseHex(theme.text), card);
    found[`${family} row name over its editing row`] = contrast(parseHex(theme.text), editing);
    found[`${family} state word over its editing row`] = contrast(words, editing);
    found[`${family} frame label over the lamp, were it unmasked`] = contrast(
      parseHex(theme["module-label"]),
      frame,
    );
  }
  return found;
}

describe("text over the brightest lit lamps", () => {
  it.each(["dark", "day"] as const)(
    "stays at 4.5:1 or more in the %s theme: rows, editing rows and frame labels",
    (name) => {
      const found = textContrasts(name);
      expect(Object.keys(found)).toHaveLength(8);
      for (const [text, ratio] of Object.entries(found)) {
        expect(ratio, `${name}: ${text}`).toBeGreaterThanOrEqual(4.5);
      }
    },
  );
});
