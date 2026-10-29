// The dot field's light, on the CPU: for each 7px cell of the screen, how much light reaches it from
// the lit nodes' boxes, and which of four levels its lamp shows after an 8 by 8 ordered dither. The
// canvas only draws the result, so everything DESIGN.md's "Dot field and rings" says about light is
// tested here without a GPU. Every number comes from a theme token.
import { lightBoxKey } from "./marker";
import type { FieldLight, FieldMode, LightSource } from "./lights";
import { breathing, motionOf, ringFade, ringRadiusAt } from "./motion";
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
  steady?: boolean;
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
type ProfileTheme = { [K in ProfileKey as `${K}-light-gain`]: number };

/** The theme values the field reads; the full theme satisfies it. */
export interface FieldTheme extends MotionTheme, ProfileTheme, Record<LightSource, string> {
  "lamp-off": string;
  "signal-light-near": string;
  "signal-light-far": string;
  "dot-cell-size": string;
  "lamp-cut-low": number;
  "lamp-cut-high": number;
  "lamp-ramp-deep": number;
  "lamp-ramp-mid": number;
  "lamp-ramp-hot": number;
  "light-near-weight": number;
  "light-far-weight": number;
  "light-source-limit": number;
}

export interface FieldConfig {
  /** A cell's side, in CSS px. */
  cell: number;
  /** The intensity under which a lamp is off, and the one at which it is whole. */
  cutLow: number;
  cutHigh: number;
  /** How a lamp's color follows its brightness: see the lamp-ramp tokens. */
  ramp: { deep: number; mid: number; hot: number };
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
  /**
   * How much light reaches each cell before it is cut into levels, 0 to 255: smooth where `data`
   * is dithered, so what must fade with the light (the rest contours) fades without blocks.
   */
  glow?: Uint8ClampedArray;
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
    cutLow: theme["lamp-cut-low"],
    cutHigh: theme["lamp-cut-high"],
    ramp: {
      deep: theme["lamp-ramp-deep"],
      mid: theme["lamp-ramp-mid"],
      hot: theme["lamp-ramp-hot"],
    },
    nearShare: theme["light-near-weight"],
    farShare: theme["light-far-weight"],
    limit: theme["light-source-limit"],
    lampOff: parseHex(theme["lamp-off"]),
    profile: {
      near: px(theme["signal-light-near"]),
      far: px(theme["signal-light-far"]),
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
    const box = boxes.get(lightBoxKey(light));
    if (!box) continue;
    // Folded methods have distinct ids but share the class row's rendered bounds.
    const key = [light.source, box.x1, box.y1, box.x2, box.y2].join(":");
    const known = placed.get(key);
    if (!known || light.weight > known.weight) {
      placed.set(key, {
        box,
        color: parseHex(theme[light.source]),
        weight: light.weight,
        pulses: light.pulses,
        pulseWeight: light.pulseWeight,
        ...(light.steady ? { steady: true } : {}),
      });
    }
  }
  return [...placed.values()];
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

/** The lowest intensity that lights a lamp: anything under the cut's low edge is off. */
function lowestLit(config: FieldConfig): number {
  return config.cutLow;
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
  const breath =
    light.steady || !light.pulses
      ? 1
      : time === null
        ? motion.breathingMax
        : breathing(motion, time);
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
  glow: Uint8ClampedArray;
  /** Per column, for the light being added: the distance to its box, and the offset from its center. */
  colX: Float32Array;
  ringX: Float32Array;
  decay: Float64Array;
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
  const half = config.motion.ringBand / 2;
  const ringStrength = config.motion.ringStrength * ringFade(config.motion, radius);
  const pulseWeight = light.pulseWeight ?? 1;
  // These bounds only reject candidates. Keep the original sqrt/edge checks below, and pad
  // squared bounds for rounding at the edge (including Float32 column offsets).
  const glowSquared = glow < 0 ? -1 : glow * glow;
  const glowBound = glowSquared + Math.abs(glowSquared) * Number.EPSILON * 4;
  const inner = Math.max(0, radius - half);
  const outer = radius + half;
  const ringPad = outer * outer * Number.EPSILON * 4;
  const ringInner = inner * inner - ringPad;
  const ringOuter = outer * outer + ringPad;
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
    const dySquared = dy * dy;
    const rySquared = ry * ry;
    const ringRow = ring && rySquared <= ringOuter;
    for (let col = c0; col <= c1; col += 1) {
      const dx = colX[col] ?? 0;
      const distanceSquared = dx * dx + dySquared;
      let intensity = 0;
      if (distanceSquared <= glowBound) {
        const distance = Math.sqrt(distanceSquared);
        if (distance <= glow)
          intensity = amplitude * cachedFalloff(config, distance, row * cols + col, work.decay);
      }
      if (ringRow) {
        const rx = ringX[col] ?? 0;
        const squared = rx * rx + rySquared;
        if (squared >= ringInner && squared <= ringOuter) {
          const offset = Math.abs(Math.sqrt(squared) - radius);
          if (offset < half)
            intensity +=
              pulseWeight * (ringStrength * (0.5 + 0.5 * Math.cos((Math.PI * offset) / half)));
        }
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
  let cells = 0;
  let decays = new Map<string, Float64Array>();
  const work = emptyWork();

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
      work.glow = new Uint8ClampedArray(cells);
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
    const retained = new Map<string, Float64Array>();
    for (const one of reaching.slice(0, config.limit)) {
      const key = decayKey(config, one.light.box, cols, rows);
      work.decay = decays.get(key) ?? new Float64Array(cells);
      retained.set(key, work.decay);
      addLight(config, cols, rows, one, time, work);
    }
    decays = retained;
    shadeLamps(config, cols, rows, work, work.data, work.glow);
    return { cols, rows, data: work.data, glow: work.glow };
  };
}

export interface Accumulated {
  sum: Float32Array;
  red: Float32Array;
  green: Float32Array;
  blue: Float32Array;
}

/** A smooth rise from 0 at `low` to 1 at `high`. */
function smooth(low: number, high: number, x: number): number {
  const t = Math.min(1, Math.max(0, (x - low) / (high - low)));
  return t * t * (3 - 2 * t);
}

/** One channel of a lit lamp: the light's, deepened if it is a weaker one, then lamp-off to it. */
function lampColor(
  channel: number,
  peak: number,
  weaker: number,
  ink: number,
  toInk: number,
  off: number,
  share: number,
): number {
  const base = channel >= peak ? channel : channel * weaker;
  return off + (base + (ink - base) * toInk - off) * share;
}

/**
 * Turns each cell's intensity into one lamp's brightness, the intensity itself held back by a smooth
 * cut between its low edge and its high one, and the brightness into a color. Dim lamps lean to the deep end of the light's
 * color, a lamp at `mid` shows it as it is, and the hot core moves `hot` of the way to the ink (the
 * extreme away from lamp-off). The colors of overlapping lights mix by intensity; their
 * intensities add, up to the full light.
 */
export function shadeLamps(
  config: FieldConfig,
  cols: number,
  rows: number,
  light: Accumulated,
  out: Uint8ClampedArray,
  glow: Uint8ClampedArray,
) {
  const { deep, mid, hot } = config.ramp;
  const [or, og, ob] = config.lampOff;
  const ink = or + og + ob > 381 ? 0 : 255;
  for (let k = 0; k < cols * rows; k += 1) {
    const total = light.sum[k] ?? 0;
    glow[k] = Math.min(1, total) * 255;
    const intensity = Math.min(1, total);
    const share = intensity * smooth(config.cutLow, config.cutHigh, intensity);
    const o = k * 4;
    if (share <= 0) {
      out[o] = or;
      out[o + 1] = og;
      out[o + 2] = ob;
      out[o + 3] = 0;
      continue;
    }
    const red = (light.red[k] ?? 0) / total;
    const green = (light.green[k] ?? 0) / total;
    const blue = (light.blue[k] ?? 0) / total;
    const peak = Math.max(red, green, blue);
    const weaker = deep + (1 - deep) * Math.min(1, share / mid);
    const toInk = share > mid ? ((share - mid) / (1 - mid)) * hot : 0;
    out[o] = lampColor(red, peak, weaker, ink, toInk, or, share);
    out[o + 1] = lampColor(green, peak, weaker, ink, toInk, og, share);
    out[o + 2] = lampColor(blue, peak, weaker, ink, toInk, ob, share);
    out[o + 3] = 255 * share;
  }
}

/** R4 uses the same physical spread in every view; only the graph camera scales it. */
export function signalConfig(config: FieldConfig, zoom: number): FieldConfig {
  return {
    ...config,
    profile: {
      near: config.profile.near * zoom,
      far: config.profile.far * zoom,
      gain: config.profile.gain,
    },
  };
}

/** Box falloff is unchanged while the clock moves; keep exact doubles, bounded to visible sources. */
function cachedFalloff(
  config: FieldConfig,
  distance: number,
  index: number,
  decay: Float64Array,
): number {
  const known = decay[index] ?? 0;
  if (known !== 0) return known;
  const value = falloff(config, distance);
  decay[index] = value;
  return value;
}

function emptyWork(): Work {
  const empty = () => new Float32Array(0);
  return {
    sum: empty(),
    red: empty(),
    green: empty(),
    blue: empty(),
    data: new Uint8ClampedArray(0),
    glow: new Uint8ClampedArray(0),
    colX: empty(),
    ringX: empty(),
    decay: new Float64Array(0),
  };
}

function decayKey(config: FieldConfig, box: Box, cols: number, rows: number): string {
  return [
    cols,
    rows,
    config.cell,
    config.profile.near,
    config.profile.far,
    config.nearShare,
    config.farShare,
    box.x1,
    box.y1,
    box.x2,
    box.y2,
  ].join(":");
}
