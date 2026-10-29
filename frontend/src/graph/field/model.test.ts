import { describe, expect, it } from "vitest";

import { themes } from "../../theme";
import type { FieldLight, FieldMode } from "./lights";
import {
  bayerMatrix,
  boxDistance,
  createLampField,
  falloff,
  fieldConfig,
  parseHex,
  placeLights,
} from "./model";
import type { Box, FieldConfig, LampGrid, PlacedLight, Rgb } from "./model";

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

/** Every cell's level over an area, row by row. */
function levels(grid: LampGrid): number[] {
  return Array.from({ length: grid.cols * grid.rows }, (_, k) =>
    level(grid, k % grid.cols, Math.floor(k / grid.cols)),
  );
}

const sum = (grid: LampGrid) => levels(grid).reduce((total, value) => total + value, 0);

describe("the dither matrix", () => {
  it("holds each of 0 to 63 once and builds from quarters offset by 0, 2, 3 and 1", () => {
    const matrix = bayerMatrix(8);
    expect([...matrix].sort((a, b) => a - b)).toEqual(Array.from({ length: 64 }, (_, k) => k));
    // The corner of the 8 by 8 matrix: 0 and 32 over 48 and 16, as the recursion gives.
    expect([matrix[0], matrix[1], matrix[8], matrix[9]]).toEqual([0, 32, 48, 16]);
    const quarterOffset = [
      [0, 0, 0],
      [4, 0, 2],
      [0, 4, 3],
      [4, 4, 1],
    ] as const;
    for (const [x, y, offset] of quarterOffset) {
      for (let row = 0; row < 4; row += 1)
        for (let col = 0; col < 4; col += 1)
          expect((matrix[(y + row) * 8 + x + col] ?? 0) % 4).toBe(offset);
    }
  });
});

describe("the field's numbers", () => {
  it("use the R4 spread tokens in every mode and keep each mode's gain", () => {
    const profile = (mode: FieldMode) => fieldConfig(themes.dark, mode).profile;
    expect(profile("watch")).toEqual({ near: 26, far: 72, gain: 0.92 });
    expect(profile("live-diff")).toEqual({ near: 26, far: 72, gain: 0.92 });
    expect(profile("file")).toEqual({ near: 26, far: 72, gain: 0.8 });
    expect(profile("review")).toEqual({ near: 26, far: 72, gain: 0.7 });
    expect(config).toMatchObject({
      cell: 7,
      levels: 4,
      ditherSize: 8,
      nearShare: 0.72,
      farShare: 0.28,
      limit: 8,
      lampOff: [5, 5, 5],
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
      0.72 * Math.exp(-1) + 0.28 * Math.exp(-near / far),
      10,
    );
    // Far out only the far term is left: its share, over its own decay.
    expect(falloff(config, 500) / (0.28 * Math.exp(-500 / far))).toBeCloseTo(1, 3);
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
    expect(color(grid, 10, 10)).toEqual([5, 5, 5]);
    expect(grid.data[3]).toBe(0);
  });

  it("carry a smooth glow beside the dithered levels: none without light, falling gently outward", () => {
    expect(lamps(config, 600, 400, [], null).glow?.every((value) => value === 0)).toBe(true);
    const grid = lamps(config, 1920, 400, [light(BOX)], null);
    const row = 25;
    const glow = Array.from({ length: 120 }, (_, i) => grid.glow?.[row * grid.cols + 40 + i] ?? 0);
    expect(glow[0]).toBeGreaterThan(200);
    expect(glow).toEqual([...glow].sort((a, b) => b - a));
    // A level is a step of 85; the glow falls by less than that from one cell to the next.
    expect(Math.max(...glow.slice(1).map((value, i) => (glow[i] ?? 0) - value))).toBeLessThan(85);
    expect(glow.at(-1)).toBeLessThan(40);
  });

  it("show one of four levels by an 8 by 8 dither: 0.92 gives 49 lamps of 64 at the top", () => {
    const grid = lamps(config, 600, 400, [light(BOX)], null);
    // Cells 20..27 by 20..27 lie inside the box, where the light is 1 x 0.92 everywhere.
    const block: number[] = [];
    for (let row = 20; row < 28; row += 1)
      for (let col = 20; col < 28; col += 1) block.push(level(grid, col, row));
    // 3 x 0.92 = 2.76: a lamp reaches level 3 where the matrix adds 0.24 or more, 49 of 64 steps.
    expect(block.filter((value) => value === 3)).toHaveLength(49);
    expect(block.filter((value) => value === 2)).toHaveLength(15);
    // The same matrix repeats every 8 cells.
    expect(level(grid, 20, 20)).toBe(level(grid, 28, 20));
    expect(level(grid, 20, 20)).toBe(level(grid, 20, 28));
  });

  it("take their color from lamp-off to the light's color by level", () => {
    const grid = lamps(config, 600, 400, [light(BOX)], null);
    for (let row = 20; row < 28; row += 1) {
      for (let col = 20; col < 28; col += 1) {
        const at = level(grid, col, row);
        const expected = ORANGE.map((channel) => Math.round(5 + (channel - 5) * (at / 3)));
        expect(color(grid, col, row), `${String(col)},${String(row)}`).toEqual(expected);
      }
    }
  });

  it("agree with the formula on every cell: nothing is lost by cutting a light off far away", () => {
    const grid = lamps(config, 700, 500, [light(BOX)], null);
    const matrix = bayerMatrix(8);
    for (let row = 0; row < grid.rows; row += 1) {
      for (let col = 0; col < grid.cols; col += 1) {
        const distance = boxDistance(BOX, (col + 0.5) * 7, (row + 0.5) * 7);
        const intensity = 0.92 * falloff(config, distance);
        const threshold = ((matrix[(row % 8) * 8 + (col % 8)] ?? 0) + 0.5) / 64;
        const expected = Math.min(3, Math.floor(Math.min(1, intensity) * 3 + threshold));
        expect(level(grid, col, row), `${String(col)},${String(row)}`).toBe(expected);
      }
    }
  });

  it("scale with the weight and the mode's gain", () => {
    const strong = sum(lamps(config, 600, 400, [light(BOX, { weight: 1 })], null));
    const weak = sum(lamps(config, 600, 400, [light(BOX, { weight: 0.36 })], null));
    const held = sum(lamps(fieldConfig(themes.dark, "review"), 600, 400, [light(BOX)], null));
    expect(weak).toBeLessThan(strong);
    // Review holds the light in with its gain of 0.70.
    expect(held).toBeLessThan(strong);
  });

  it("mix the colors of overlapping lights by intensity", () => {
    const grid = lamps(
      config,
      600,
      400,
      [light(BOX, { weight: 1 }), light(BOX, { weight: 0.5, color: BLUE })],
      null,
    );
    // Inside the box the intensities are 0.92 and 0.46: together past full, so the top level, in
    // the mix of the two colors weighted by those intensities (two thirds orange, one third blue).
    const [r, g, b] = color(grid, 24, 24);
    expect(level(grid, 24, 24)).toBe(3);
    expect(r).toBeCloseTo((255 * 2 + 79) / 3, 0);
    expect(g).toBeCloseTo((92 * 2 + 125) / 3, 0);
    expect(b).toBeCloseTo((31 * 2 + 255) / 3, 0);
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
    // 0.92 x 0.72 = 0.66: 63 lamps at level 2 and one at 1; 0.92 x 1: 49 at level 3, 15 at 2.
    expect(block(lamps(config, 600, 400, [pulsing], 0))).toBe(63 * 2 + 1);
    expect(block(lamps(config, 600, 400, [pulsing], 1.45))).toBe(49 * 3 + 15 * 2);
    // The still light, with no clock, is the full weight.
    expect(block(lamps(config, 600, 400, [pulsing], null))).toBe(49 * 3 + 15 * 2);
    // A light that does not pulse ignores the clock.
    expect(block(lamps(config, 600, 400, [light(BOX)], 0))).toBe(49 * 3 + 15 * 2);
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
    expect(at(grid, 110, 3)).toBeGreaterThan(1.8);
    expect(at(grid, 60, 3)).toBeLessThan(at(grid, 110, 3) - 1);
    expect(at(grid, 160, 3)).toBeLessThan(at(grid, 110, 3) - 1);
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
