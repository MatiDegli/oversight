import { describe, expect, it } from "vitest";

import { themes } from "../../theme";
import type { Pulse } from "./afterlight";
import { pulseLights } from "./pulseLights";
import { createLampField, fieldConfig, parseHex } from "./model";
import { maskLamps } from "./mask";

const pulse: Pulse = {
  key: "merge-a",
  merge: { key: "merge-a", id: "node", slot: "A", source: "agent-a", pulse: true, age: 0 },
  progress: 0.5,
  opacity: 1,
};
const paths = new Map([
  [
    pulse.key,
    [
      { x: 10, y: 10 },
      { x: 110, y: 10 },
      { x: 110, y: 110 },
    ],
  ],
]);
const emit = (pulses: readonly Pulse[] = [pulse]) => pulseLights(pulses, paths, themes.dark);

describe("merge pulse point lights", () => {
  it("places the head and five trailing lights by arc length around a corner", () => {
    // easeOut(0.5) = 0.875: 175 px along the 200 px path.
    const expected = [
      [110, 85],
      [110, 65],
      [110, 45],
      [110, 25],
      [105, 10],
      [85, 10],
    ];
    emit().forEach((point, k) => {
      expect(point.x).toBeCloseTo(expected[k]?.[0] ?? NaN, 10);
      expect(point.y).toBeCloseTo(expected[k]?.[1] ?? NaN, 10);
    });
  });

  it("pins the sketch's intensity curve, radius, count and team without changing its inputs", () => {
    const before = structuredClone(pulse);
    const lights = emit();
    expect(lights).toHaveLength(6);
    lights.forEach((light, k) => {
      expect(light.intensity).toBeCloseTo(0.95 * (1 - k / 6) ** 1.4, 12);
      expect(light.radius).toBe(19);
      expect(light.source).toBe("agent-a");
    });
    expect(pulse).toEqual(before);
  });

  it("clamps trailing points at the start instead of extrapolating", () => {
    const lights = emit([{ ...pulse, progress: 0.001 }]);
    expect(lights[0]?.x).toBeGreaterThan(10);
    expect(lights.slice(1).map(({ x, y }) => [x, y])).toEqual(Array(5).fill([10, 10]));
  });

  it("caps the shared source at 12 lights and preserves each merge's team", () => {
    const b = { ...pulse, key: "b", merge: { ...pulse.merge, source: "agent-b" as const } };
    const c = { ...pulse, key: "c" };
    const routes = new Map([
      ...paths,
      [b.key, paths.get(pulse.key) ?? []],
      [c.key, paths.get(pulse.key) ?? []],
    ]);
    const lights = pulseLights([pulse, b, c], routes, themes.dark);
    expect(lights).toHaveLength(12);
    expect(lights.slice(0, 6).every((light) => light.source === "agent-a")).toBe(true);
    expect(lights.slice(6).every((light) => light.source === "agent-b")).toBe(true);
  });

  it("emits nothing without travel, a usable path, or after the head arrives", () => {
    expect(emit([])).toEqual([]);
    expect(pulseLights([pulse], new Map(), themes.dark)).toEqual([]);
    expect(pulseLights([pulse], new Map([[pulse.key, [{ x: 0, y: 0 }]]]), themes.dark)).toEqual([]);
    for (const progress of [0, 1, 1.1]) expect(emit([{ ...pulse, progress }])).toEqual([]);
    expect(emit([{ ...pulse, opacity: 0 }])).toEqual([]);
  });
});

it.each(["dark", "day"] as const)(
  "mixes pulse gaussians before the common dither and %s color ramp",
  (name) => {
    const theme = themes[name];
    const config = fieldConfig(theme, "watch");
    const color = parseHex(theme["agent-b"]);
    const light = { x: 73.5, y: 73.5, intensity: 0.95, radius: 19, source: "agent-b" as const };
    const field = createLampField()(config, 147, 147, [], null, [light]);
    const at = (x: number, y: number) => (Math.floor(y / 7) * field.cols + Math.floor(x / 7)) * 4;
    const head = at(light.x, light.y);
    expect(field.data[head + 3]).toBeGreaterThan(0);
    // B's blue channel dominates under both ramps, and every level remains a square in the same grid.
    expect(field.data[head + 2]).toBeGreaterThan(field.data[head] ?? 0);
    expect(color[2]).toBeGreaterThan(color[0]);
    expect(config.cell).toBe(7);
    expect(new Set(Array.from(field.data).filter((_, i) => i % 4 === 3))).toEqual(
      new Set([0, 85, 170, 255]),
    );
    expect(field.glow?.[Math.floor(head / 4)]).toBeCloseTo(242, 0);
    // Weak gaussian tails bypass box-light cutoffs, and only the 3-radius boundary cuts them.
    expect(field.glow?.[at(129.5, 73.5) / 4]).toBeGreaterThan(0);
    // Beyond three radii, even the continuous pre-dither intensity is zero.
    expect(field.glow?.[at(136.5, 73.5) / 4]).toBe(0);
    maskLamps(field, config.cell, [{ x1: 70, y1: 70, x2: 77, y2: 77 }]);
    expect(field.data[head + 3]).toBe(1); // Reserved masked value; the renderer suppresses the LED.
  },
);

it("adds overlapping point lights before quantizing them into levels", () => {
  const config = fieldConfig(themes.dark, "watch");
  const lamps = createLampField();
  const a = { x: 3.5, y: 3.5, intensity: 0.2, radius: 19, source: "agent-a" as const };
  const b = { ...a, source: "agent-b" as const };
  expect(lamps(config, 7, 7, [], null, [a]).data[3]).toBe(0);
  expect(lamps(config, 7, 7, [], null, [b]).data[3]).toBe(0);
  expect(lamps(config, 7, 7, [], null, [a, b]).data[3]).toBe(85);
});
