import { describe, expect, it } from "vitest";

import { themes } from "../../theme";
import type { Box, LampGrid } from "./model";
import { coverHeights, createHeightField, hillsOf, moundsOf, terrainConfig, unit } from "./terrain";
import type { Heights, TerrainModule } from "./terrain";

const config = terrainConfig(themes.dark);
const frame = (x: number, y: number, w = 260, h = 180): Box => ({
  x1: x,
  y1: y,
  x2: x + w,
  y2: y + h,
});
const modules: TerrainModule[] = [
  { id: "shop.cart", callers: 1, frame: frame(100, 300) },
  { id: "shop.pricing", callers: 9, frame: frame(700, 150) },
  { id: "shop.models", callers: 0, frame: frame(1200, 600) },
];
const W = 1600;
const H = 900;
const heights = createHeightField();
const at = (grid: Heights, x: number, y: number) =>
  grid.data[(Math.round(y / grid.cell) * grid.cols + Math.round(x / grid.cell)) * 2] ?? 0;
const centerOf = (box: Box) => [(box.x1 + box.x2) / 2, (box.y1 + box.y2) / 2] as const;
const peak = (id: string, time: number | null = null) => {
  const found = modules.find((module) => module.id === id);
  if (!found) throw new Error(id);
  const grid = heights(config, hillsOf(config, modules), W, H, time);
  const [x, y] = centerOf(found.frame);
  return at(grid, x, y);
};

describe("one hill per module", () => {
  it("puts a hill on each module, centered on its frame", () => {
    const hills = hillsOf(config, modules);
    expect(hills).toHaveLength(3);
    for (const module of modules) {
      const [x, y] = centerOf(module.frame);
      expect(hills.some((hill) => hill.x === x && hill.y === y)).toBe(true);
    }
  });

  it("grows a hill's height with its callers, over a small range that no hill escapes", () => {
    const low = peak("shop.models");
    const middle = peak("shop.cart");
    const high = peak("shop.pricing");
    expect(low).toBeGreaterThan(config.base * 0.85);
    expect(high).toBeLessThan((config.base + config.range) * 1.2);
    expect(middle).toBeGreaterThan(low);
    expect(high).toBeGreaterThan(middle);
    expect(high - low).toBeLessThan(config.range * 1.4);
  });

  it("rises to a few lines, never more than the level cap, and falls outward", () => {
    const grid = heights(config, hillsOf(config, modules), W, H, null);
    const [cx, cy] = centerOf(modules[1]?.frame ?? frame(0, 0));
    const along = Array.from({ length: 40 }, (_, i) => at(grid, cx + i * 16, cy));
    const levels = new Set(along.map((h) => Math.floor(h / config.step)));
    expect(levels.size).toBeGreaterThan(2);
    expect(Math.max(...levels)).toBeLessThanOrEqual(config.levels);
    // The hill wanders a little and the ground bends, so compare points well apart.
    const apart = [0, 10, 20, 39].map((i) => along[i] ?? 0);
    expect(apart).toEqual([...apart].sort((x, y) => y - x));
    expect(along.at(-1) ?? 1).toBeLessThan(config.step);
  });

  it("builds nothing for no modules, and only the soft mounds fill the ground", () => {
    expect(hillsOf(config, [])).toEqual([]);
    const mounds = moundsOf(config, [], W, H);
    expect(mounds.length).toBeGreaterThan(0);
    expect(mounds.length).toBeLessThanOrEqual(config.mounds);
    for (const mound of mounds) expect(mound.height).toBeLessThan(config.base);
  });

  it("keeps the mounds in the empty areas, apart from each other, and the same every time", () => {
    const frames = modules.map((module) => module.frame);
    const mounds = moundsOf(config, frames, W, H);
    expect(moundsOf(config, frames, W, H)).toEqual(mounds);
    for (const mound of mounds)
      expect(
        frames.some(
          (f) => mound.x >= f.x1 && mound.x <= f.x2 && mound.y >= f.y1 && mound.y <= f.y2,
        ),
      ).toBe(false);
    for (const [i, a] of mounds.entries())
      for (const b of mounds.slice(i + 1))
        expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThan(50);
  });
});

describe("the drift", () => {
  const sample = (time: number | null) => {
    const hills = hillsOf(config, modules);
    return Float32Array.from(heights(config, hills, W, H, time).data);
  };
  const largest = (a: Float32Array, b: Float32Array) =>
    a.reduce((most, value, i) => Math.max(most, Math.abs(value - (b[i] ?? 0))), 0);

  it("never jumps: a second changes the ground by far less than a line", () => {
    for (const time of [0, 45, 300]) {
      expect(largest(sample(time), sample(time + 1))).toBeLessThan(config.step / 20);
    }
  });

  it("changes shape over minutes, by more than a line", () => {
    expect(largest(sample(0), sample(120))).toBeGreaterThan(config.step / 3);
    expect(largest(sample(0), sample(240))).toBeGreaterThan(config.step / 3);
  });

  it("is still without a clock: the same ground every time, the ground of time zero", () => {
    expect(largest(sample(null), sample(null))).toBe(0);
    expect(largest(sample(null), sample(0))).toBe(0);
  });

  it("moves less in the same time when the speed token is smaller", () => {
    const slow = { ...config, speed: config.speed / 4 };
    const hills = hillsOf(config, modules);
    const fast = Float32Array.from(heights(config, hills, W, H, 60).data);
    const calm = Float32Array.from(heights(slow, hills, W, H, 60).data);
    const start = Float32Array.from(heights(config, hills, W, H, 0).data);
    expect(largest(start, calm)).toBeLessThan(largest(start, fast));
  });
});

describe("the grid", () => {
  it("covers the screen plus an edge and reuses its array", () => {
    const first = heights(config, [], W, H, null);
    expect(first.cols * config.cell).toBeGreaterThanOrEqual(W);
    expect(first.rows * config.cell).toBeGreaterThanOrEqual(H);
    const second = heights(config, [], W, H, null);
    expect(second.data).toBe(first.data);
    expect(second.data.every((value) => value === 0)).toBe(true);
  });

  it("reads its numbers from the theme tokens, in both themes, gray only", () => {
    for (const theme of [themes.dark, themes.day]) {
      const read = terrainConfig(theme);
      expect(read.cell).toBe(16);
      expect(read.line.alpha).toBeLessThan(0.12);
      const [r, g, b] = read.line.color;
      expect(Math.max(r, g, b) - Math.min(r, g, b)).toBeLessThan(8);
    }
  });

  it("gives the same number for the same name and a different one for another", () => {
    expect(unit("shop.cart")).toBe(unit("shop.cart"));
    expect(unit("shop.cart")).not.toBe(unit("shop.pricing"));
    expect(unit("shop.cart", 3)).not.toBe(unit("shop.cart", 4));
    expect(unit("x")).toBeGreaterThanOrEqual(0);
    expect(unit("x")).toBeLessThan(1);
  });
});

describe("the cover of light", () => {
  it("follows the lamps' smooth glow, and covers nothing without one", () => {
    const grid = heights(config, [], 200, 100, null);
    const cols = 30;
    const glow = new Uint8ClampedArray(cols * 15);
    glow[2 * cols + 4] = 255;
    glow[2 * cols + 6] = 51;
    const lamps: LampGrid = { cols, rows: 15, data: new Uint8ClampedArray(cols * 15 * 4), glow };
    coverHeights(grid, lamps, 7);
    const cover = (x: number, y: number) =>
      grid.data[(Math.round(y / grid.cell) * grid.cols + Math.round(x / grid.cell)) * 2 + 1];
    // The node at 32px, 16px lies over lamp cell 4, 2 (full) and the one at 48px over cell 6, 2.
    expect(cover(32, 16)).toBe(1);
    expect(cover(48, 16)).toBeCloseTo(51 / 255, 5);
    expect(cover(0, 0)).toBe(0);
    expect(cover(192, 96)).toBe(0);
    coverHeights(grid, { cols, rows: 15, data: lamps.data }, 7);
    expect(grid.data.filter((_, i) => i % 2 === 1).every((value) => value === 0)).toBe(true);
  });
});
