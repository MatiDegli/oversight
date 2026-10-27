// The dot field's light, on the CPU: for each 7px cell of the screen, how much light reaches it from
// the lit nodes' boxes, and which of four levels its lamp shows after an 8 by 8 ordered dither. The
// canvas only draws the result, so everything DESIGN.md's "Dot field and rings" says about light is
// tested here without a GPU. Every number comes from a theme token.
import type { FieldLight, FieldMode, LightSource } from "./lights";
import { breathing, motionOf, ringIntensity, ringRadiusAt } from "./motion";
import type { Motion, MotionTheme } from "./motion";

/** A color as red, green and blue from 0 to 255. */
export type Rgb = readonly [number, number, number];

/** A node's rendered box, in CSS pixels from the graph element's top left. */
export interface Box {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

/** A light with the box it was drawn at and the color it takes from its token. */
export interface PlacedLight {
  box: Box;
  color: Rgb;
  weight: number;
  pulses: boolean;
  pulseWeight?: number;
}

/** How far a mode's light reaches: the near and far decay lengths in px, and the mode's gain. */
export interface LightProfile {
  near: number;
  far: number;
  gain: number;
}

type ProfileKey = "watch" | "live-diff" | "file-view" | "review";
type ProfileTheme = {
  [K in ProfileKey as `${K}-light-near` | `${K}-light-far`]: string;
} & { [K in ProfileKey as `${K}-light-gain`]: number };

/** The theme values the field reads; the full theme satisfies it. */
export interface FieldTheme extends MotionTheme, ProfileTheme, Record<LightSource, string> {
  "lamp-off": string;
  "dot-cell-size": string;
  "dot-level-count": number;
  "dot-dither-size": number;
  "light-near-weight": number;
  "light-far-weight": number;
  "light-source-limit": number;
}

export interface FieldConfig {
  /** A cell's side, in CSS px. */
  cell: number;
  /** The levels a lamp can show, off included. */
  levels: number;
  ditherSize: number;
  /** The shares of a light's intensity that decay over the near and the far length. */
  nearShare: number;
  farShare: number;
  /** The most lights mixed at once. */
  limit: number;
  lampOff: Rgb;
  profile: LightProfile;
  motion: Motion;
}

/** The lamps of the whole screen: one RGBA cell each, row by row from the top left. */
export interface LampGrid {
  cols: number;
  rows: number;
  /** A lamp's color, and as alpha its level: 0 off, 255 the full light. Reused by the next call. */
  data: Uint8ClampedArray;
}

const px = Number.parseFloat;

const profileKey: Record<FieldMode, ProfileKey> = {
  watch: "watch",
  "live-diff": "live-diff",
  file: "file-view",
  review: "review",
};

export function parseHex(hex: string): Rgb {
  const value = Number.parseInt(hex.replace("#", "").slice(0, 6), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

export function fieldConfig(theme: FieldTheme, mode: FieldMode): FieldConfig {
  const key = profileKey[mode];
  return {
    cell: px(theme["dot-cell-size"]),
    levels: theme["dot-level-count"],
    ditherSize: theme["dot-dither-size"],
    nearShare: theme["light-near-weight"],
    farShare: theme["light-far-weight"],
    limit: theme["light-source-limit"],
    lampOff: parseHex(theme["lamp-off"]),
    profile: {
      near: px(theme[`${key}-light-near`]),
      far: px(theme[`${key}-light-far`]),
      gain: theme[`${key}-light-gain`],
    },
    motion: motionOf(theme),
  };
}

/** One light per visible row and source, keeping the strongest projected light and its motion. */
export function placeLights(
  theme: Record<LightSource, string>,
  lights: readonly FieldLight[],
  boxes: ReadonlyMap<string, Box>,
): PlacedLight[] {
  const placed = new Map<string, PlacedLight>();
  for (const light of lights) {
    const box = boxes.get(light.id);
    if (!box) continue;
    // Folded methods have distinct ids but share the class row's rendered bounds.
    const key = `${light.source}:${box.x1},${box.y1},${box.x2},${box.y2}`;
    const known = placed.get(key);
    if (!known || light.weight > known.weight) {
      placed.set(key, {
        box,
        color: parseHex(theme[light.source]),
        weight: light.weight,
        pulses: light.pulses,
        pulseWeight: light.pulseWeight,
      });
    }
  }
  return [...placed.values()];
}

const matrices = new Map<number, Uint8Array>();

/**
 * The ordered-dither matrix of a power-of-two size: each of 0 to size² - 1 once, built by the
 * recursive rule that doubles a matrix into four quarters offset by 0, 2, 3 and 1.
 */
export function bayerMatrix(size: number): Uint8Array {
  const known = matrices.get(size);
  if (known) return known;
  let matrix = [0];
  for (let side = 1; side < size; side *= 2) {
    const next = new Array<number>(4 * side * side).fill(0);
    for (let y = 0; y < side; y += 1) {
      for (let x = 0; x < side; x += 1) {
        const base = 4 * (matrix[y * side + x] ?? 0);
        next[y * 2 * side + x] = base;
        next[y * 2 * side + x + side] = base + 2;
        next[(y + side) * 2 * side + x] = base + 3;
        next[(y + side) * 2 * side + x + side] = base + 1;
      }
    }
    matrix = next;
  }
  const result = Uint8Array.from(matrix);
  matrices.set(size, result);
  return result;
}

/** Distance from a point to a box: 0 inside it. The light is shaped like the box. */
export function boxDistance(box: Box, x: number, y: number): number {
  const dx = Math.max(box.x1 - x, 0, x - box.x2);
  const dy = Math.max(box.y1 - y, 0, y - box.y2);
  return Math.sqrt(dx * dx + dy * dy);
}

/** What share of a light's strength reaches `distance` from its box: a near term and a far one. */
export function falloff(config: FieldConfig, distance: number): number {
  const { near, far } = config.profile;
  return (
    config.nearShare * Math.exp(-distance / near) + config.farShare * Math.exp(-distance / far)
  );
}

/**
 * The lowest intensity that can lift a lamp to its first level: the dither adds at least half a
 * step of the matrix to a level's share, so anything below this is off whatever the cell.
 */
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
export function createLampField() {
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
