import { describe, expect, expectTypeOf, it } from "vitest";

import { themes } from "../../theme";
import type { FieldLight, FieldMode } from "./lights";
import {
  boxDistance,
  createLampField,
  falloff,
  fieldConfig,
  bayerMatrix,
  parseHex,
  placeLights,
  shadeLamps,
} from "./model";
import type { Accumulated, Box, FieldConfig, LampGrid, PlacedLight, Rgb } from "./model";

const ORANGE: Rgb = [255, 92, 31];
const BLUE: Rgb = [79, 125, 255];
const config = fieldConfig(themes.dark, "watch");

const light = (box: Box, over: Partial<PlacedLight> = {}): PlacedLight => ({
  box,
  color: ORANGE,
  weight: 1,
  pulses: false,
  ...over,
});
/** A box covering whole cells 20..39 by 20..29: eight-cell blocks of it have one distance, 0. */
const BOX: Box = { x1: 140, y1: 140, x2: 280, y2: 210 };

function level(grid: LampGrid, col: number, row: number): number {
  return Math.round(((grid.data[(row * grid.cols + col) * 4 + 3] ?? 0) / 255) * 3);
}

function color(grid: LampGrid, col: number, row: number): number[] {
  const at = (row * grid.cols + col) * 4;
  return [grid.data[at] ?? 0, grid.data[at + 1] ?? 0, grid.data[at + 2] ?? 0];
}

/**
 * The sketch's ordered dither, transcribed from life.html (`bayer2`, `bayer4`, `bayer8`): each
 * floors its coordinates and recurses on half of them.
 */
const bayer2 = (x: number, y: number) => {
  const value = Math.floor(x) / 2 + Math.floor(y) ** 2 * 0.75;
  return value - Math.floor(value);
};
const bayer4 = (x: number, y: number) => bayer2(0.5 * x, 0.5 * y) * 0.25 + bayer2(x, y);
const bayer8 = (x: number, y: number) => bayer4(0.5 * x, 0.5 * y) * 0.25 + bayer2(x, y);

/** Every cell's level over an area, row by row. */
function levels(grid: LampGrid): number[] {
  return Array.from({ length: grid.cols * grid.rows }, (_, k) =>
    level(grid, k % grid.cols, Math.floor(k / grid.cols)),
  );
}

const sum = (grid: LampGrid) => levels(grid).reduce((total, value) => total + value, 0);

describe("the field's numbers", () => {
  it("use the R4 spread tokens in every mode and keep each mode's gain", () => {
    const profile = (mode: FieldMode) => fieldConfig(themes.dark, mode).profile;
    expect(profile("watch")).toEqual({ near: 26, far: 72, gain: 0.92 });
    expect(profile("live-diff")).toEqual({ near: 26, far: 72, gain: 0.92 });
    expect(profile("file")).toEqual({ near: 26, far: 72, gain: 0.8 });
    expect(profile("review")).toEqual({ near: 26, far: 72, gain: 0.7 });
    expect(config).toMatchObject({
      cell: 7,
      cutLow: 0.08,
      cutHigh: 0.22,
      levels: 4,
      ditherSize: 8,
      ramp: { deep: 0.45, mid: 0.7, hot: 0.2 },
      nearShare: 0.72,
      farShare: 0.28,
      limit: 8,
      lampOff: [7, 7, 7],
    });
  });

  it("reads colors as hex", () => {
    expect(parseHex("#ff5c1f")).toEqual([255, 92, 31]);
    expect(parseHex("#000000")).toEqual([0, 0, 0]);
  });
});

describe("the light's shape", () => {
  it("measures distance from the box, zero inside it", () => {
    const box = { x1: 10, y1: 10, x2: 30, y2: 20 };
    expect(boxDistance(box, 20, 15)).toBe(0);
    expect(boxDistance(box, 10, 20)).toBe(0);
    expect(boxDistance(box, 40, 15)).toBe(10);
    expect(boxDistance(box, 20, 0)).toBe(10);
    expect(boxDistance(box, 33, 24)).toBe(5);
  });

  it("is 72% a near term and 28% a far one, so the near decays quickly and the far spreads", () => {
    expect(falloff(config, 0)).toBeCloseTo(1, 10);
    const { near, far } = config.profile;
    expect(falloff(config, near)).toBeCloseTo(
      0.72 * Math.exp(-1) + 0.28 / (1 + (near / far) ** 2),
      10,
    );
    // Far out only the far term is left: its share over one plus the square of its length.
    expect(falloff(config, 500) / (0.28 / (1 + (500 / far) ** 2))).toBeCloseTo(1, 3);
    let previous = Infinity;
    for (let d = 0; d < 400; d += 5) {
      expect(falloff(config, d)).toBeLessThan(previous);
      previous = falloff(config, d);
    }
  });
});

describe("the lamps", () => {
  const lamps = createLampField();

  it("are off, in lamp-off, where nothing is lit, one cell per 7px", () => {
    const grid = lamps(config, 1920, 1080, [], null);
    expect([grid.cols, grid.rows]).toEqual([275, 155]);
    expect(levels(grid).every((value) => value === 0)).toBe(true);
    expect(color(grid, 10, 10)).toEqual([7, 7, 7]);
    expect(grid.data[3]).toBe(0);
  });

  it("carry a smooth glow beside the lamps: none without light, falling gently outward", () => {
    expect(lamps(config, 600, 400, [], null).glow?.every((value) => value === 0)).toBe(true);
    const grid = lamps(config, 1920, 400, [light(BOX)], null);
    const row = 25;
    const glow = Array.from({ length: 120 }, (_, i) => grid.glow?.[row * grid.cols + 40 + i] ?? 0);
    expect(glow[0]).toBeGreaterThan(200);
    expect(glow).toEqual([...glow].sort((a, b) => b - a));
    // The glow falls by a small step from one cell to the next.
    expect(Math.max(...glow.slice(1).map((value, i) => (glow[i] ?? 0) - value))).toBeLessThan(50);
    expect(glow.at(-1)).toBeLessThan(40);
  });

  it("use the sketch's 8 by 8 ordered dither, cell for cell", () => {
    const matrix = bayerMatrix(8);
    for (let y = 0; y < 8; y += 1)
      for (let x = 0; x < 8; x += 1) expect(matrix[y * 8 + x]).toBe(bayer8(x, y) * 64);
    expect(new Set(matrix).size).toBe(64);
  });

  it("show four levels: whole near the core, and further out single lamps in a regular pattern", () => {
    const grid = lamps(config, 1920, 400, [light(BOX)], null);
    const alpha = (col: number, row: number) => grid.data[(row * grid.cols + col) * 4 + 3] ?? 0;
    // Only four brightnesses exist, and all four are used.
    const seen = new Set<number>();
    for (let k = 0; k < grid.cols * grid.rows; k += 1) seen.add(grid.data[k * 4 + 3] ?? 0);
    expect([...seen].sort((a, b) => a - b)).toEqual([0, 85, 170, 255]);
    // Right beside the box the light is 0.92: three quarters of the lamps whole, the rest two thirds.
    const near = levels(grid).filter((_, k) => {
      const col = k % grid.cols;
      const row = Math.floor(k / grid.cols);
      return col >= 20 && col < 28 && row >= 20 && row < 28;
    });
    expect(near.filter((value) => value === 3)).toHaveLength(48);
    expect(near.filter((value) => value === 2)).toHaveLength(16);
    // Past the cut's low edge a lamp is off, and the light stays near its card.
    expect(alpha(grid.cols - 1, 25)).toBe(0);
  });

  it("leave single lamps in a regular pattern where the light is thin", () => {
    const cols = 16;
    const rows = 8;
    const cells = cols * rows;
    const thin = (intensity: number) => {
      const accumulated: Accumulated = {
        strongest: new Float32Array(cells).fill(intensity),
        sum: new Float32Array(cells).fill(intensity),
        red: new Float32Array(cells).fill(intensity * 255),
        green: new Float32Array(cells).fill(intensity * 92),
        blue: new Float32Array(cells).fill(intensity * 31),
      };
      const data = new Uint8ClampedArray(cells * 4);
      shadeLamps(config, cols, rows, accumulated, data, new Uint8ClampedArray(cells));
      return Array.from({ length: cells }, (_, k) => data[k * 4 + 3] ?? 0);
    };
    // At 0.1 the dither lights the 19 of 64 cells whose matrix value is 45 or more, as lamps of
    // the first level, and the two 8 by 8 blocks side by side show the same cells.
    const first = thin(0.1);
    expect(first.filter((value) => value > 0)).toHaveLength(2 * 19);
    expect(new Set(first)).toEqual(new Set([0, 85]));
    for (let row = 0; row < rows; row += 1)
      for (let col = 0; col < 8; col += 1)
        expect(first[row * cols + col]).toBe(first[row * cols + col + 8]);
    // At 0.5 half of them are at the second level and the rest at the first: none off.
    const half = thin(0.5);
    expect(half.filter((value) => value === 170)).toHaveLength(2 * 32);
    expect(half.filter((value) => value === 85)).toHaveLength(2 * 32);
  });

  it("agree with the sketch's formula on every cell: each light cut, then the dithered level", () => {
    const grid = lamps(config, 700, 500, [light(BOX)], null);
    let lit = 0;
    for (let row = 0; row < grid.rows; row += 1) {
      for (let col = 0; col < grid.cols; col += 1) {
        const distance = boxDistance(BOX, (col + 0.5) * 7, (row + 0.5) * 7);
        const intensity = 0.92 * falloff(config, distance);
        const t = Math.min(1, Math.max(0, (intensity - 0.08) / (0.22 - 0.08)));
        const total = Math.fround(intensity * t * t * (3 - 2 * t));
        const expected = Math.floor(Math.min(1, total) * 3 + bayer8(col, row));
        expect(level(grid, col, row), `${String(col)},${String(row)}`).toBe(expected);
        if (expected > 0) lit += 1;
      }
    }
    expect(lit).toBeGreaterThan(100);
  });

  it("scale with the weight and the mode's gain", () => {
    const strong = sum(lamps(config, 600, 400, [light(BOX, { weight: 1 })], null));
    const weak = sum(lamps(config, 600, 400, [light(BOX, { weight: 0.36 })], null));
    const held = sum(lamps(fieldConfig(themes.dark, "review"), 600, 400, [light(BOX)], null));
    expect(weak).toBeLessThan(strong);
    // Review holds the light in with its gain of 0.70.
    expect(held).toBeLessThan(strong);
  });

  it("uses the strongest source hue while overlapping intensities add", () => {
    const grid = lamps(
      config,
      600,
      400,
      [light(BOX, { weight: 1 }), light(BOX, { weight: 0.5, color: BLUE })],
      null,
    );
    // 0.92 + 0.46 still makes a whole lamp; hue is now orange, never their mixture.
    const [r, g, b] = color(grid, 24, 24);
    expect(level(grid, 24, 24)).toBe(3);
    const mixed: number[] = [...ORANGE];
    const hot = (channel: number) => channel + (255 - channel) * 0.2;
    expect(r).toBeCloseTo(hot(mixed[0] ?? 0), 0);
    expect(g).toBeCloseTo(hot(mixed[1] ?? 0), 0);
    expect(b).toBeCloseTo(hot(mixed[2] ?? 0), 0);
  });

  it("color a lamp along its level: deep when dim, the agent's own at the middle, light when whole", () => {
    // One cell at the matrix's first value (0), so a level is the floor of three times the light.
    const shade = (rgb: Rgb, intensity: number, cfg: FieldConfig = config) => {
      const accumulated: Accumulated = {
        strongest: Float32Array.of(intensity),
        sum: Float32Array.of(intensity),
        red: Float32Array.of(intensity * rgb[0]),
        green: Float32Array.of(intensity * rgb[1]),
        blue: Float32Array.of(intensity * rgb[2]),
      };
      const data = new Uint8ClampedArray(4);
      shadeLamps(cfg, 1, 1, accumulated, data, new Uint8ClampedArray(1));
      return [...data];
    };
    // Off under the first level; then a third, two thirds, and the whole light.
    expect(shade(ORANGE, 0.2)).toEqual([7, 7, 7, 0]);
    const dim = shade(ORANGE, 0.34);
    const mid = shade(ORANGE, 0.67);
    const whole = shade(ORANGE, 1);
    expect([dim[3], mid[3], whole[3]]).toEqual([85, 170, 255]);
    // Dim: the weaker channels fall faster than the strong one, so the lamp leans to deep red.
    const dimRatio = ((dim[1] ?? 0) - 7) / ((dim[0] ?? 1) - 7);
    // Without the ramp this ratio is (92 - 7) / (255 - 7) = 0.34; with it, well under a quarter.
    expect(dimRatio).toBeLessThan(0.25);
    expect(dim[0]).toBeLessThan(mid[0] ?? 0);
    // Whole: brighter and lighter than the agent's color, a fifth of the way to white.
    expect(whole).toEqual([255, 125, 76, 255]);
    // The same ramp in the agent's blue: its strong channel stays, the whole level is lighter.
    const blueDim = shade(BLUE, 0.34);
    expect(shade(BLUE, 1)).toEqual([114, 151, 255, 255]);
    expect(((blueDim[0] ?? 0) - 7) / ((blueDim[2] ?? 1) - 7)).toBeLessThan(0.2);
    // On the day ground the whole level goes the other way, toward ink.
    const dayConfig = fieldConfig(themes.day, "watch");
    expect(shade([178, 60, 10], 1, dayConfig)).toEqual([142, 48, 8, 255]);
  });

  it("mix at most 8 lights: the strongest of those on the screen", () => {
    const at = (index: number): Box => ({
      x1: 60 + index * 120,
      y1: 100,
      x2: 100 + index * 120,
      y2: 130,
    });
    const litAround = (grid: LampGrid, index: number) => {
      const box = at(index);
      const col = Math.floor((box.x1 + box.x2) / 2 / 7);
      const row = Math.floor((box.y1 + box.y2) / 2 / 7);
      return level(grid, col, row) > 0 || level(grid, col + 1, row) > 0;
    };
    // Nine lights, strongest first; the ninth, the weakest, is left out.
    const nine = Array.from({ length: 9 }, (_, index) =>
      light(at(index), { weight: 1 - index * 0.05 }),
    );
    const grid = lamps(config, 1200, 300, nine, null);
    expect(Array.from({ length: 9 }, (_, index) => litAround(grid, index))).toEqual([
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      true,
      false,
    ]);
    // A strong light off the screen does not take one of the 8 places.
    const away = light({ x1: 5000, y1: 5000, x2: 5040, y2: 5030 }, { weight: 1 });
    const eight = Array.from({ length: 8 }, (_, index) =>
      light(at(index), { weight: 0.5 - index * 0.01 }),
    );
    const kept = lamps(config, 1200, 300, [away, ...eight], null);
    expect(Array.from({ length: 8 }, (_, index) => litAround(kept, index))).toEqual(
      Array(8).fill(true),
    );
  });

  it("place lights only where their nodes are drawn", () => {
    const boxes = new Map([["a", BOX]]);
    const placed = placeLights(
      themes.dark,
      [
        { id: "a", source: "agent-b", weight: 0.4, pulses: true },
        { id: "gone", source: "agent-a", weight: 1, pulses: false },
      ],
      boxes,
    );
    expect(placed).toEqual([{ box: BOX, color: BLUE, weight: 0.4, pulses: true }]);
  });

  it.each([false, true])("keep the strongest light on a folded row (reversed: %s)", (reverse) => {
    // Hidden methods have separate box objects with the folded class row's same bounds.
    const boxes = new Map([
      ["Cart.add", { ...BOX }],
      ["Cart.total", { ...BOX }],
    ]);
    const lights: FieldLight[] = [
      { id: "Cart.add", source: "agent-a", weight: 0.36, pulses: false },
      { id: "Cart.total", source: "agent-a", weight: 1, pulses: true, pulseWeight: 0.8 },
    ];
    const placed = placeLights(themes.dark, reverse ? lights.toReversed() : lights, boxes);
    const strongest = light(BOX, { pulses: true, pulseWeight: 0.8 });
    expect(placed).toEqual([strongest]);
    const lamps = createLampField();
    const actual = [...lamps(config, 600, 400, placed, null).data];
    expect(actual).toEqual([...lamps(config, 600, 400, [strongest], null).data]);
  });

  it("keep separate sources on a folded row and separate visible rows", () => {
    const other = { ...BOX, y1: 220, y2: 290 };
    const placed = placeLights(
      themes.dark,
      [
        { id: "Cart.add", source: "agent-a", weight: 0.36, pulses: false },
        { id: "Cart.total", source: "agent-a", weight: 0.72, pulses: false },
        { id: "Cart.add", source: "agent-b", weight: 1, pulses: true },
        { id: "Cart.total", source: "work-light", weight: 0.52, pulses: false },
        { id: "quote", source: "agent-a", weight: 0.36, pulses: false },
      ],
      new Map([
        ["Cart.add", { ...BOX }],
        ["Cart.total", { ...BOX }],
        ["quote", other],
      ]),
    );
    expect(placed).toEqual([
      light(BOX, { weight: 0.72 }),
      light(BOX, { color: BLUE, pulses: true }),
      light(BOX, { color: parseHex(themes.dark["work-light"]), weight: 0.52 }),
      light(other, { weight: 0.36 }),
    ]);
  });
});

describe("the lamps in motion", () => {
  const lamps = createLampField();
  const small: Box = { x1: 390, y1: 295, x2: 410, y2: 305 };
  const mean = (grid: LampGrid, area: (col: number, row: number) => boolean) => {
    const picked = levels(grid).filter((_, k) => area(k % grid.cols, Math.floor(k / grid.cols)));
    return picked.reduce((total, value) => total + value, 0) / picked.length;
  };

  it("breathe: an editing light swings between 72% and 100% of its weight", () => {
    // Cells 20..27 by 20..27 lie inside the box and well away from where the ring is at these times.
    const block = (grid: LampGrid) => {
      let total = 0;
      for (let row = 20; row < 28; row += 1)
        for (let col = 20; col < 28; col += 1) total += level(grid, col, row);
      return total;
    };
    const pulsing = light(BOX, { pulses: true });
    // The 64 lamps are one whole matrix period. 0.92 x 0.72 = 0.66 puts 63 of them at two thirds
    // and the one with matrix value 0 at one third; 0.92 x 1 puts 48 whole and 16 at two thirds.
    expect(block(lamps(config, 600, 400, [pulsing], 0))).toBe(63 * 2 + 1);
    expect(block(lamps(config, 600, 400, [pulsing], 1.45))).toBe(48 * 3 + 16 * 2);
    // The still light, with no clock, is the full weight.
    expect(block(lamps(config, 600, 400, [pulsing], null))).toBe(48 * 3 + 16 * 2);
    // A light that does not pulse ignores the clock.
    expect(block(lamps(config, 600, 400, [light(BOX)], 0))).toBe(48 * 3 + 16 * 2);
  });

  it("send a ring from the node's center at 55 px per second, in the agent's color", () => {
    const pulsing = light(small, { pulses: true });
    const at = (grid: LampGrid, radius: number, band: number) =>
      mean(grid, (col, row) => {
        const d = Math.hypot((col + 0.5) * 7 - 400, (row + 0.5) * 7 - 300);
        return Math.abs(d - radius) <= band;
      });
    // At 2 s the ring is 110 px out: its band is lit, and 50 px inside or outside it is not.
    const grid = lamps(config, 800, 600, [pulsing], 2);
    expect(at(grid, 110, 3)).toBeGreaterThan(1.4);
    expect(at(grid, 60, 3)).toBeLessThan(at(grid, 110, 3) - 0.5);
    expect(at(grid, 160, 3)).toBeLessThan(at(grid, 110, 3) - 0.5);
    // A cell on the band is in the agent's color, not a neutral one.
    const col = Math.round((400 + 110) / 7 - 0.5);
    const row = Math.round(300 / 7 - 0.5);
    const [r = 0, , b = 0] = color(grid, col, row);
    expect(r).toBeGreaterThan(b);
    // A second later it has moved to 165 px.
    const later = lamps(config, 800, 600, [pulsing], 3);
    expect(at(later, 165, 3)).toBeGreaterThan(1.2);
    expect(at(later, 110, 3)).toBeLessThan(at(later, 165, 3));
  });

  it("send no ring from a light that does not pulse, or with no clock", () => {
    const ring = (grid: LampGrid) =>
      mean(
        grid,
        (col, row) => Math.abs(Math.hypot((col + 0.5) * 7 - 400, (row + 0.5) * 7 - 300) - 110) <= 3,
      );
    expect(ring(lamps(config, 800, 600, [light(small)], 2))).toBeLessThan(0.5);
    expect(ring(lamps(config, 800, 600, [light(small, { pulses: true })], null))).toBeLessThan(0.5);
  });

  it("fade the ring out as it grows: far out it is gone", () => {
    const pulsing = light(small, { pulses: true });
    const band = (grid: LampGrid, radius: number) =>
      mean(
        grid,
        (col, row) =>
          Math.abs(Math.hypot((col + 0.5) * 7 - 400, (row + 0.5) * 7 - 300) - radius) <= 3,
      );
    // The field reuses its grid on every call, so each one is read before the next is made.
    const near = band(lamps(config, 800, 600, [pulsing], 1.5), 82.5);
    const far = band(lamps(config, 800, 600, [pulsing], 5.9), 324.5);
    expect(near).toBeGreaterThan(1.5);
    expect(far).toBeLessThan(0.5);
  });
});

describe("a config without lights", () => {
  it("stays valid for every mode", () => {
    const lamps = createLampField();
    for (const mode of ["watch", "live-diff", "file", "review"] as const) {
      const modeConfig: FieldConfig = fieldConfig(themes.dark, mode);
      expect(levels(lamps(modeConfig, 70, 70, [], null))).toEqual(Array(100).fill(0));
    }
  });
});

it("invalidates cached falloff after zoom, box movement, profile and viewport changes", () => {
  const cached = createLampField();
  for (const [width, height, zoom, x, nearShare] of [
    [600, 400, 1, 140, 0.72],
    [400, 600, 1, 140, 0.72],
    [600, 400, 0.5, 140, 0.72],
    [600, 400, 1, 155, 0.72],
    [600, 400, 1, 155, 0.5],
    [600, 400, 1, 140, 0.72],
  ] as const) {
    const changed = {
      ...config,
      nearShare,
      profile: { ...config.profile, near: 26 * zoom, far: 72 * zoom },
    };
    const placed = [light({ ...BOX, x1: x, x2: x + 140 }, { pulses: true })];
    expect([...cached(changed, width, height, placed, 2).data]).toEqual([
      ...createLampField()(changed, width, height, placed, 2).data,
    ]);
  }
});

it("requires strongest-source storage on an accumulator", () => {
  expectTypeOf<Accumulated["strongest"]>().toEqualTypeOf<Float32Array>();
});

it("gives two resting teams a clean edge with unchanged additive brightness", () => {
  const grid = createLampField()(
    config,
    600,
    400,
    [light(BOX), light({ ...BOX, x1: 300, x2: 440 }, { color: BLUE })],
    null,
  );
  for (const [col, rgb] of [
    [24, ORANGE],
    [52, BLUE],
  ] as const) {
    expect(level(grid, col, 23)).toBe(3);
    expect(color(grid, col, 23)).toEqual(rgb.map((v) => Math.round(v + (255 - v) * 0.2)));
  }
});
