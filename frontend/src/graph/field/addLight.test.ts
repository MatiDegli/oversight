// Frozen reference from bb059a5. Only this test keeps the old addLight loop; the surrounding
// pipeline stays here too, so byte comparisons include accumulation, clipping and dithering.
import { describe, expect, it } from "vitest";

import { themes } from "../../theme";
import { bayerMatrix, createLampField, falloff, fieldConfig } from "./model";
import type { Box, FieldConfig, LampGrid, PlacedLight } from "./model";
import { breathing, ringIntensity, ringRadiusAt } from "./motion";

function lowestLit(config: FieldConfig): number {
  return 0.5 / (config.ditherSize * config.ditherSize * (config.levels - 1));
}

/**
 * How far from its box a light can still light a lamp, in px; -1 when it never does. Each term is
 * cut where it falls to half the lowest lit intensity, so together they stay under it.
 */
function reach(config: FieldConfig, amplitude: number): number {
  const floor = lowestLit(config);
  if (amplitude <= floor) return -1;
  const { near, far } = config.profile;
  return Math.max(
    near * Math.log((2 * config.nearShare * amplitude) / floor),
    far * Math.log((2 * config.farShare * amplitude) / floor),
    0,
  );
}

/**
 * The light's strength before falloff: its weight and the mode's gain, times its breath while it
 * moves. Still, a light that would breathe holds its full weight.
 */
function amplitudeOf(config: FieldConfig, light: PlacedLight, time: number | null): number {
  const { motion, profile } = config;
  const breath = !light.pulses ? 1 : time === null ? motion.breathingMax : breathing(motion, time);
  return light.weight * profile.gain * (1 + (breath - 1) * (light.pulseWeight ?? 1));
}

/** The box of pixels a light can touch: its glow around its box, and its ring around its center. */
function extent(config: FieldConfig, light: PlacedLight, glow: number, ring: boolean): Box {
  const { box } = light;
  const covered: Box[] = [];
  if (glow >= 0) {
    covered.push({ x1: box.x1 - glow, y1: box.y1 - glow, x2: box.x2 + glow, y2: box.y2 + glow });
  }
  if (ring) {
    const cx = (box.x1 + box.x2) / 2;
    const cy = (box.y1 + box.y2) / 2;
    const out = config.motion.ringRadius + config.motion.ringBand / 2;
    covered.push({ x1: cx - out, y1: cy - out, x2: cx + out, y2: cy + out });
  }
  return {
    x1: Math.min(...covered.map((part) => part.x1)),
    y1: Math.min(...covered.map((part) => part.y1)),
    x2: Math.max(...covered.map((part) => part.x2)),
    y2: Math.max(...covered.map((part) => part.y2)),
  };
}

/** The arrays a frame is accumulated in, kept between frames. */
interface Work extends Accumulated {
  data: Uint8ClampedArray;
  /** Per column, for the light being added: the distance to its box, and the offset from its center. */
  colX: Float32Array;
  ringX: Float32Array;
}

/** A light that reaches the screen, with what its glow and ring need. */
interface Reaching {
  light: PlacedLight;
  amplitude: number;
  glow: number;
  ring: boolean;
  box: Box;
}

/** Adds one light's glow, and its ring, to the cells it reaches. */
function addLight(
  config: FieldConfig,
  cols: number,
  rows: number,
  { light, amplitude, glow, ring, box }: Reaching,
  time: number | null,
  work: Work,
) {
  const { sum, red, green, blue, colX, ringX } = work;
  const c0 = Math.max(0, Math.floor(box.x1 / config.cell));
  const c1 = Math.min(cols - 1, Math.floor(box.x2 / config.cell));
  const r0 = Math.max(0, Math.floor(box.y1 / config.cell));
  const r1 = Math.min(rows - 1, Math.floor(box.y2 / config.cell));
  const cx = (light.box.x1 + light.box.x2) / 2;
  const cy = (light.box.y1 + light.box.y2) / 2;
  const radius = ring && time !== null ? ringRadiusAt(config.motion, time) : 0;
  for (let col = c0; col <= c1; col += 1) {
    const x = (col + 0.5) * config.cell;
    colX[col] = Math.max(light.box.x1 - x, 0, x - light.box.x2);
    ringX[col] = x - cx;
  }
  const [lr, lg, lb] = light.color;
  for (let row = r0; row <= r1; row += 1) {
    const y = (row + 0.5) * config.cell;
    const dy = Math.max(light.box.y1 - y, 0, y - light.box.y2);
    const ry = y - cy;
    for (let col = c0; col <= c1; col += 1) {
      const dx = colX[col] ?? 0;
      const distance = Math.sqrt(dx * dx + dy * dy);
      let intensity = distance <= glow ? amplitude * falloff(config, distance) : 0;
      if (ring) {
        const rx = ringX[col] ?? 0;
        intensity +=
          (light.pulseWeight ?? 1) *
          ringIntensity(config.motion, radius, Math.sqrt(rx * rx + ry * ry));
      }
      if (intensity <= 0) continue;
      const k = row * cols + col;
      sum[k] = (sum[k] ?? 0) + intensity;
      red[k] = (red[k] ?? 0) + intensity * lr;
      green[k] = (green[k] ?? 0) + intensity * lg;
      blue[k] = (blue[k] ?? 0) + intensity * lb;
    }
  }
}

/**
 * Makes the function that lights the cells. It keeps its working arrays between calls, and the
 * grid it returns is one of them: it is valid until the next call.
 */
function createLegacyLampField() {
  const empty = () => new Float32Array(0);
  let cells = 0;
  const work: Work = {
    sum: empty(),
    red: empty(),
    green: empty(),
    blue: empty(),
    data: new Uint8ClampedArray(0),
    colX: empty(),
    ringX: empty(),
  };

  /**
   * Lights the cells of a `width` by `height` screen. `time` is the clock in seconds, or null for
   * the still light: no breathing and no rings. At most `config.limit` lights are mixed: of the
   * ones that reach the screen, the strongest.
   */
  return function lamps(
    config: FieldConfig,
    width: number,
    height: number,
    lights: readonly PlacedLight[],
    time: number | null,
  ): LampGrid {
    const cols = Math.max(1, Math.ceil(width / config.cell));
    const rows = Math.max(1, Math.ceil(height / config.cell));
    if (cols * rows !== cells) {
      cells = cols * rows;
      work.sum = new Float32Array(cells);
      work.red = new Float32Array(cells);
      work.green = new Float32Array(cells);
      work.blue = new Float32Array(cells);
      work.data = new Uint8ClampedArray(cells * 4);
    } else {
      work.sum.fill(0);
      work.red.fill(0);
      work.green.fill(0);
      work.blue.fill(0);
    }
    if (work.colX.length < cols) {
      work.colX = new Float32Array(cols);
      work.ringX = new Float32Array(cols);
    }
    const reaching = lights.flatMap((light): Reaching[] => {
      const amplitude = amplitudeOf(config, light, time);
      const ring = time !== null && light.pulses;
      const glow = reach(config, amplitude);
      const box = extent(config, light, glow, ring);
      const onScreen = box.x2 >= 0 && box.y2 >= 0 && box.x1 <= width && box.y1 <= height;
      return onScreen && (glow >= 0 || ring) ? [{ light, amplitude, glow, ring, box }] : [];
    });
    reaching.sort((a, b) => b.light.weight - a.light.weight);
    for (const one of reaching.slice(0, config.limit))
      addLight(config, cols, rows, one, time, work);
    quantize(config, cols, rows, work, work.data);
    return { cols, rows, data: work.data };
  };
}

interface Accumulated {
  sum: Float32Array;
  red: Float32Array;
  green: Float32Array;
  blue: Float32Array;
}

const dithers = new Map<number, Uint8Array>();
/** The dither matrix of a size, made once. */
function ditherOf(size: number): Uint8Array {
  let matrix = dithers.get(size);
  if (!matrix) {
    matrix = bayerMatrix(size);
    dithers.set(size, matrix);
  }
  return matrix;
}

/**
 * Turns each cell's intensity into a level with the ordered dither, and the level into a color
 * between lamp-off and the cell's mixed light color. The colors of overlapping lights mix by
 * intensity; their intensities add, up to the full light.
 */
function quantize(
  config: FieldConfig,
  cols: number,
  rows: number,
  light: Accumulated,
  out: Uint8ClampedArray,
) {
  const top = config.levels - 1;
  const matrix = ditherOf(config.ditherSize);
  const steps = config.ditherSize * config.ditherSize;
  const [or, og, ob] = config.lampOff;
  for (let row = 0; row < rows; row += 1) {
    const rowOffset = (row % config.ditherSize) * config.ditherSize;
    for (let col = 0; col < cols; col += 1) {
      const k = row * cols + col;
      const total = light.sum[k] ?? 0;
      let level = 0;
      if (total > 0) {
        const threshold = ((matrix[rowOffset + (col % config.ditherSize)] ?? 0) + 0.5) / steps;
        level = Math.min(top, Math.floor(Math.min(1, total) * top + threshold));
      }
      const o = k * 4;
      if (level === 0) {
        out[o] = or;
        out[o + 1] = og;
        out[o + 2] = ob;
        out[o + 3] = 0;
      } else {
        const share = level / top;
        out[o] = or + ((light.red[k] ?? 0) / total - or) * share;
        out[o + 1] = og + ((light.green[k] ?? 0) / total - og) * share;
        out[o + 2] = ob + ((light.blue[k] ?? 0) / total - ob) * share;
        out[o + 3] = 255 * share;
      }
    }
  }
}

const fixedLights: PlacedLight[] = [
  {
    box: { x1: 411.125, y1: 288.375, x2: 579.875, y2: 322.625 },
    color: [255, 92, 31],
    weight: 1,
    pulses: true,
  },
  {
    box: { x1: 512.3, y1: 348.7, x2: 690.6, y2: 382.2 },
    color: [79, 125, 255],
    weight: 0.72,
    pulses: true,
    pulseWeight: 0.37,
  },
  {
    box: { x1: -120.2, y1: -15.4, x2: 48.8, y2: 18.6 },
    color: [255, 92, 31],
    weight: 0.4,
    pulses: true,
  },
  {
    box: { x1: 1860.1, y1: 1065.8, x2: 2028.9, y2: 1099.2 },
    color: [79, 125, 255],
    weight: 0.36,
    pulses: false,
  },
];

function assertSame(config: FieldConfig, lights: PlacedLight[], time: number | null) {
  const before = createLegacyLampField();
  const after = createLampField();
  // Reuse the accumulators, resize them, and change column count without changing cell count.
  for (const [width, height] of [
    [1920, 1080],
    [1920, 1080],
    [777, 511],
    [511, 777],
    [1920, 1080],
  ] as const) {
    const expected = before(config, width, height, lights, time);
    const actual = after(config, width, height, lights, time);
    expect([actual.cols, actual.rows]).toEqual([expected.cols, expected.rows]);
    expect(Buffer.compare(Buffer.from(actual.data), Buffer.from(expected.data))).toBe(0);
  }
}

describe.each(["watch", "live-diff", "file", "review"] as const)(
  "%s grid matches bb059a5 byte for byte",
  (mode) => {
    const config = fieldConfig(themes.dark, mode);
    it("glow only", () => {
      assertSame(
        config,
        fixedLights.map((light) => ({ ...light, pulses: false })),
        2,
      );
    });
    it("ring only, including the center, fade and wrap", () => {
      for (const time of [0, 0.000001, 2, 6, 340 / 55, 8.125])
        assertSame(
          config,
          fixedLights.map((light) => ({ ...light, weight: 0, pulses: true })),
          time,
        );
    });
    it("glow and ring overlap, with fractional pulse weights and offscreen boxes", () => {
      for (const time of [0, 1.45, 2, 6.18, 8.125]) assertSame(config, fixedLights, time);
    });
    it("still light has full breathing weight and no ring with time null", () => {
      assertSame(config, fixedLights, null);
    });
    it("clips partly offscreen rings and skips lights beyond the screen", () => {
      assertSame(config, fixedLights.slice(2), 2);
      assertSame(
        config,
        fixedLights.map((light) => ({
          ...light,
          box: { x1: -2000, y1: -2000, x2: -1800, y2: -1800 },
        })),
        2,
      );
    });
    it("keeps Float32 offsets at the annulus boundaries, including a zero pulse envelope", () => {
      for (const pulseWeight of [0, 0.37, 1]) {
        // For centers 308.5 and 318.5, the cell at 423.5 is 115 (outer) and 105 (inner) px out.
        for (const cx of [308.5, 308.500001, 308.499999, 318.5, 318.500001, 318.499999]) {
          assertSame(
            config,
            [
              {
                box: { x1: cx - 84, y1: 301.5, x2: cx + 84, y2: 335.5 },
                color: [255, 92, 31],
                weight: 0.72,
                pulses: true,
                pulseWeight,
              },
            ],
            2,
          );
        }
      }
    });
  },
);

// Opt-in timing, with no speed assertion (from frontend/):
// FIELD_BENCH=1 pnpm exec vitest run --project unit src/graph/field/addLight.test.ts -t benchmarks --disableConsoleIntercept
it.skipIf(process.env.FIELD_BENCH !== "1")(
  "benchmarks repeated lamps calls at 1920x1080 with the demo's eight-light limit",
  () => {
    const config = fieldConfig(themes.dark, "live-diff");
    const lights = Array.from({ length: config.limit }, (_, i): PlacedLight => ({
      box: {
        x1: 260 + (i % 4) * 360.125,
        y1: 260 + Math.floor(i / 4) * 420.375,
        x2: 428 + (i % 4) * 360.125,
        y2: 294 + Math.floor(i / 4) * 420.375,
      },
      color: i % 2 ? [79, 125, 255] : [255, 92, 31],
      weight: i < 2 ? 1 : 0.4,
      pulses: i < 2,
    }));
    const fields = [createLegacyLampField(), createLampField()];
    const samples: number[][] = [[], []];
    for (const lamps of fields) for (let i = 0; i < 100; i++) lamps(config, 1920, 1080, lights, 2);
    for (let repeat = 0; repeat < 31; repeat++) {
      // Alternate order to keep warmup and concurrent machine load from favoring either side.
      for (const index of repeat % 2 ? [1, 0] : [0, 1]) {
        const lamps = fields[index];
        if (!lamps) throw new Error("Missing benchmark field");
        const start = performance.now();
        for (let i = 0; i < 20; i++) lamps(config, 1920, 1080, lights, 2);
        samples[index]?.push((performance.now() - start) / 20);
      }
    }
    const medians = samples.map((times) => times.sort((a, b) => a - b)[15]);
    console.info(
      "lamps median ms/call (31 batches x 20, 100 warmups, 1920x1080, 8 lights, before/after):",
      medians,
    );
    expect(medians.every((value) => value !== undefined && value > 0)).toBe(true);
  },
  30_000,
);
