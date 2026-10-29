import { describe, expect, it } from "vitest";

import { themes } from "../../theme";
import type { LampGrid, PlacedLight } from "./model";
import { createLampField, fieldConfig, parseHex } from "./model";
import { sketchBusy } from "./sketchScene.fixture";

/** What hides lamps in the sketch: opaque rows and folded cards, then the header and chip masks. */
const hidden = [...sketchBusy.covers, ...sketchBusy.masks];

/** Lamps at levels 0, 1/3, 2/3 and 1 over the cells whose centers nothing hides: frame bodies count. */
function histogram(grid: LampGrid): number[] {
  const counts = [0, 0, 0, 0];
  for (let row = 0; row < grid.rows; row += 1) {
    for (let col = 0; col < grid.cols; col += 1) {
      const x = (col + 0.5) * 7;
      const y = (row + 0.5) * 7;
      if (hidden.some((b) => x > b.x1 && x < b.x2 && y > b.y1 && y < b.y2)) continue;
      const level = Math.round(((grid.data[(row * grid.cols + col) * 4 + 3] ?? 0) / 255) * 3);
      counts[level] = (counts[level] ?? 0) + 1;
    }
  }
  return counts;
}

describe("the field on the sketch's busy scene", () => {
  const config = fieldConfig(themes.dark, "watch");
  // The sketch spreads 0.9 of a light's weight; the watch mode's gain is 0.92.
  const lights: PlacedLight[] = sketchBusy.lights.map((one) => ({
    box: one.box,
    color: parseHex(themes.dark[one.source]),
    weight: one.weight,
    pulses: false,
  }));
  const grid = createLampField()(config, sketchBusy.width, sketchBusy.height, lights, null);

  it("counts the four lamp levels like the sketch's own pass, over every cell no row, card or header hides", () => {
    const [off, first, second, whole] = histogram(grid);
    const [sketchOff, sketchFirst, sketchSecond, sketchWhole] = sketchBusy.counts;
    // The same cells are counted, so the four counts add up to the same total.
    expect((off ?? 0) + (first ?? 0) + (second ?? 0) + (whole ?? 0)).toBe(
      sketchOff + sketchFirst + sketchSecond + sketchWhole,
    );
    // The frames are translucent, so the light shows in their bodies around the lit rows: some
    // hundreds of first-level lamps, some hundreds at the second, and a hundred or so whole. The
    // product's watch gain is 0.92 against the sketch's 0.9, which puts a few more lamps whole.
    expect((first ?? 0) / sketchFirst).toBeGreaterThan(0.9);
    expect((first ?? 0) / sketchFirst).toBeLessThan(1.1);
    expect(Math.abs((second ?? 0) - sketchSecond)).toBeLessThanOrEqual(15);
    expect(sketchWhole).toBeGreaterThan(100);
    expect(Math.abs((whole ?? 0) - sketchWhole) / sketchWhole).toBeLessThan(0.15);
    expect((off ?? 0) / sketchOff).toBeGreaterThan(0.99);
  });

  it("lights the rows the agents touch, the editing row more than the one last read", () => {
    const lit = (box: { x1: number; y1: number; x2: number; y2: number }, margin: number) => {
      let total = 0;
      for (let row = 0; row < grid.rows; row += 1)
        for (let col = 0; col < grid.cols; col += 1) {
          const x = (col + 0.5) * 7;
          const y = (row + 0.5) * 7;
          const outside =
            x > box.x1 - margin &&
            x < box.x2 + margin &&
            y > box.y1 - margin &&
            y < box.y2 + margin;
          if (outside) total += (grid.data[(row * grid.cols + col) * 4 + 3] ?? 0) / 85;
        }
      return total;
    };
    const [editing, read] = [sketchBusy.lights[0], sketchBusy.lights[1]];
    // Within one fringe of each row, the editing row carries more light than the read row above it.
    expect(lit(editing.box, 40)).toBeGreaterThan(lit(read.box, 40));
  });
});
