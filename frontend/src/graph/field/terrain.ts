// The rest ground: heights of an imagined landscape, from which the shader draws contour lines. One
// hill rises over each module (its height grows with the calls into the module, over a small
// range), and a few soft mounds fill the empty areas. The heights live on a coarse grid of the
// screen and the GPU samples them smoothly, so the cost does not grow with the graph. Time moves
// the hills and bends the ground a little, in a cycle of minutes: the change is continuous, and
// without a clock (reduced motion) the same ground stands still. Every number comes from a token.
import type { Box, Rgb } from "./model";
import { parseHex } from "./model";

/** The theme values the terrain reads; the full theme satisfies it. */
export interface TerrainTheme {
  "contour-line": { color: string; alpha: number };
  "contour-step": number;
  "contour-hill-base": number;
  "contour-hill-range": number;
  "contour-hill-spread": number;
  "contour-mound-count": number;
  "contour-drift-speed": number;
  "contour-cell-size": string;
  "contour-levels": number;
  "contour-fade": number;
  "contour-line-width": string;
}

export interface TerrainConfig {
  /** The height between two lines, and the most lines above the ground. */
  step: number;
  levels: number;
  base: number;
  range: number;
  spread: number;
  mounds: number;
  speed: number;
  /** A grid cell's side in CSS px. */
  cell: number;
  /** The share of the line's alpha the outermost line keeps; the highest line keeps all of it. */
  fade: number;
  /** The line's width in CSS px. */
  width: number;
  line: { color: Rgb; alpha: number };
}

export function terrainConfig(theme: TerrainTheme): TerrainConfig {
  return {
    step: theme["contour-step"],
    levels: theme["contour-levels"],
    base: theme["contour-hill-base"],
    range: theme["contour-hill-range"],
    spread: theme["contour-hill-spread"],
    mounds: theme["contour-mound-count"],
    speed: theme["contour-drift-speed"],
    cell: Number.parseFloat(theme["contour-cell-size"]),
    fade: theme["contour-fade"],
    width: Number.parseFloat(theme["contour-line-width"]),
    line: { color: parseHex(theme["contour-line"].color), alpha: theme["contour-line"].alpha },
  };
}

/** A module's rendered frame, and how many calls from other modules reach into it. */
export interface TerrainModule {
  id: string;
  callers: number;
  frame: Box;
}

/** A rise of the ground: a smooth bump with its own pace of drift. */
export interface Hill {
  x: number;
  y: number;
  /** The spread of the bump along each axis, in px. */
  sx: number;
  sy: number;
  height: number;
  /** Numbers in 0 to 1 that set this hill's phases and paces, from its name. */
  seed: number;
}

/** The ground on a grid: `cols` by `rows` heights, row by row, sampled at multiples of `cell`. */
export interface Heights {
  cols: number;
  rows: number;
  cell: number;
  data: Float32Array;
  /** Counts the grids made, so the renderer uploads only a new one. */
  stamp: number;
}

/** The pixels around a frame that a hill spreads past it, before the spread share. */
const MARGIN = 70;
/** A hill adds nothing beyond sqrt(CUTOFF) spreads: there it is under a third of a percent of its height. */
const CUTOFF = 12;
/** One cycle of the drift, in seconds, at speed 1. */
const CYCLE = 330;
const TAU = Math.PI * 2;
/** How far the ground bends, in px, and over what lengths. */
const WARP = 22;
const WAVE_X = 410;
const WAVE_Y = 340;
/** How far a hill wanders from its place, in px. */
const WANDER = 14;

/** A stable number in 0 to 1 from a name and a salt. */
export function unit(name: string, salt = 0): number {
  let hash = 2166136261 ^ salt;
  for (let i = 0; i < name.length; i++) hash = Math.imul(hash ^ name.charCodeAt(i), 16777619);
  hash ^= hash >>> 15;
  hash = Math.imul(hash, 2246822519);
  hash ^= hash >>> 13;
  return (hash >>> 0) / 4294967296;
}

/** The hills for the modules: one each, centered on its frame, taller with more callers. */
export function hillsOf(config: TerrainConfig, modules: readonly TerrainModule[]): Hill[] {
  const most = Math.max(0, ...modules.map((module) => module.callers));
  return [...modules]
    .sort((a, b) => a.id.localeCompare(b.id))
    .map(({ id, callers, frame }) => ({
      x: (frame.x1 + frame.x2) / 2,
      y: (frame.y1 + frame.y2) / 2,
      sx: config.spread * ((frame.x2 - frame.x1) / 2 + MARGIN),
      sy: config.spread * ((frame.y2 - frame.y1) / 2 + MARGIN),
      height: config.base + (most > 0 ? config.range * (callers / most) : 0),
      seed: unit(id),
    }));
}

/** The n-th number of the van der Corput sequence in a base: evenly spread, the same every time. */
function sequence(n: number, base: number): number {
  let result = 0;
  let scale = 1 / base;
  for (let rest = n; rest > 0; rest = Math.floor(rest / base)) {
    result += (rest % base) * scale;
    scale /= base;
  }
  return result;
}

/**
 * Soft mounds in the empty areas of a `width` by `height` screen: at most `config.mounds`, each
 * clear of the frames and of each other. The places come from a fixed sequence, so they stay put.
 */
export function moundsOf(
  config: TerrainConfig,
  frames: readonly Box[],
  width: number,
  height: number,
): Hill[] {
  const mounds: Hill[] = [];
  const clear = 0.1 * Math.min(width, height);
  for (let n = 1; n < config.mounds * 12 && mounds.length < config.mounds; n++) {
    const x = sequence(n, 2) * width;
    const y = sequence(n, 3) * height;
    const crowded =
      frames.some(
        (frame) =>
          x > frame.x1 - clear &&
          x < frame.x2 + clear &&
          y > frame.y1 - clear &&
          y < frame.y2 + clear,
      ) || mounds.some((other) => Math.hypot(other.x - x, other.y - y) < clear * 2);
    if (crowded) continue;
    const seed = unit(String(n), 7);
    const spread = (0.7 + 0.6 * unit(String(n), 11)) * config.spread * 130;
    mounds.push({
      x,
      y,
      sx: spread,
      sy: spread * (0.8 + 0.4 * unit(String(n), 13)),
      height: config.base * (0.5 + 0.3 * seed),
      seed,
    });
  }
  return mounds;
}

/** Where a hill stands and how tall it is at `time` seconds: it wanders and breathes slowly. */
function driftOf(config: TerrainConfig, hill: Hill, time: number) {
  const rate = (TAU * config.speed * time) / CYCLE;
  const phase = (salt: number) => TAU * unit(String(hill.seed), salt);
  const pace = (salt: number) => 0.6 + 0.8 * unit(String(hill.seed), salt);
  return {
    x: hill.x + WANDER * Math.sin(rate * pace(21) + phase(22)),
    y: hill.y + WANDER * 0.85 * Math.cos(rate * pace(23) + phase(24)),
    height: hill.height * (1 + 0.07 * Math.sin(rate * pace(25) + phase(26))),
  };
}

/**
 * Makes the function that computes the grid. It keeps its arrays between calls, and the grid it
 * returns is valid until the next call.
 */
export function createHeightField() {
  let data = new Float32Array(0);
  let warped = new Float32Array(0);
  let stamp = 0;
  /**
   * The heights of a `width` by `height` screen at `time` seconds, or still when `time` is null.
   * Overlapping hills add up, so two near modules rise into one larger hill.
   */
  return function heights(
    config: TerrainConfig,
    hills: readonly Hill[],
    width: number,
    height: number,
    time: number | null,
  ): Heights {
    const { cell } = config;
    const cols = Math.ceil(width / cell) + 2;
    const rows = Math.ceil(height / cell) + 2;
    if (data.length !== cols * rows) {
      data = new Float32Array(cols * rows);
      warped = new Float32Array(cols * rows * 2);
    } else data.fill(0);
    const t = time ?? 0;
    const rate = (TAU * config.speed * t) / CYCLE;
    for (let row = 0; row < rows; row++)
      for (let col = 0; col < cols; col++) {
        const x = col * cell;
        const y = row * cell;
        const at = (row * cols + col) * 2;
        warped[at] = x + WARP * Math.sin((TAU * y) / WAVE_X + rate * 1.1 + 0.7);
        warped[at + 1] = y + WARP * Math.sin((TAU * x) / WAVE_Y - rate * 0.9 + 2.1);
      }
    for (const hill of hills) {
      const here = driftOf(config, hill, t);
      const reachX = Math.sqrt(CUTOFF) * hill.sx + WARP;
      const reachY = Math.sqrt(CUTOFF) * hill.sy + WARP;
      const c0 = Math.max(0, Math.floor((here.x - reachX) / cell));
      const c1 = Math.min(cols - 1, Math.ceil((here.x + reachX) / cell));
      const r0 = Math.max(0, Math.floor((here.y - reachY) / cell));
      const r1 = Math.min(rows - 1, Math.ceil((here.y + reachY) / cell));
      const kx = 1 / (2 * hill.sx * hill.sx);
      const ky = 1 / (2 * hill.sy * hill.sy);
      for (let row = r0; row <= r1; row++)
        for (let col = c0; col <= c1; col++) {
          const k = row * cols + col;
          const dx = (warped[k * 2] ?? 0) - here.x;
          const dy = (warped[k * 2 + 1] ?? 0) - here.y;
          const q = dx * dx * kx + dy * dy * ky;
          if (q < CUTOFF / 2) data[k] = (data[k] ?? 0) + here.height * Math.exp(-q);
        }
    }
    stamp += 1;
    return { cols, rows, cell, data, stamp };
  };
}
