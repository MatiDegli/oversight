// The dot field's life on the page: it listens to Cytoscape through the box source, draws when the
// graph or the lights change, and runs animation frames only while something breathes or a ring
// spreads, the tab is visible and motion is allowed. Where WebGL is missing it draws nothing, and
// the graph's background stays plain ground.
import { getActiveTheme } from "../../theme";

import type { BoxSource } from "./boxSource";
import { createLampField, fieldConfig, parseHex, placeLights } from "./model";
import type { FieldConfig, FieldTheme, LampGrid } from "./model";
import { fieldClock, shouldAnimate } from "./motion";
import { createLampRenderer } from "./renderer";
import type { LampLook, LampRenderer } from "./renderer";
import { sameLights } from "./lights";
import type { FieldLight, FieldMode } from "./lights";

/** What the field is told: the mode's light profile and the lights to draw. */
export interface FieldInput {
  mode: FieldMode;
  lights: readonly FieldLight[];
}

/** The look of the lamps, from the theme. */
export interface LookTheme {
  "dot-cell-size": string;
  "dot-lamp-coverage": number;
  "guide-dot-size": string;
  "guide-dot-spacing": string;
  "guide-dot": { color: string; alpha: number };
}

const px = Number.parseFloat;

export function lampLook(theme: LookTheme): LampLook {
  const cell = px(theme["dot-cell-size"]);
  return {
    cell,
    coverage: theme["dot-lamp-coverage"],
    guideSize: px(theme["guide-dot-size"]),
    guideEvery: Math.max(1, Math.round(px(theme["guide-dot-spacing"]) / cell)),
    guide: { color: parseHex(theme["guide-dot"].color), alpha: theme["guide-dot"].alpha },
  };
}

/**
 * What tests and `make shots` ask of the field. A canvas does not tell what it drew, so this
 * read-only view says it, the way the graph's probe does for Cytoscape.
 */
export interface FieldProbe {
  /** "webgl" while the lamps are drawn, "plain" where there is no WebGL: plain ground. */
  state(): "webgl" | "plain";
  /** True while animation frames are scheduled, for the breathing or a ring. */
  animating(): boolean;
  /** How many times the field has drawn. */
  draws(): number;
  /** The lamp of the cell at a point, in CSS px from the graph's top left, as last computed. */
  lamp(x: number, y: number): { level: number; color: [number, number, number] } | null;
  /** The pixel the GPU draws at a point, read back after drawing again; null without WebGL. */
  pixel(x: number, y: number): [number, number, number, number] | null;
}

export type FieldHost = HTMLCanvasElement & { oversightField?: FieldProbe };

export interface FieldController {
  update(input: FieldInput): void;
  /** Draws again at the current size, boxes and clock. */
  redraw(): void;
  dispose(): void;
}

type Theme = FieldTheme & LookTheme;

export function createFieldController(canvas: FieldHost, source: BoxSource): FieldController {
  const lamps = createLampField();
  // The dev override holds the clock still, so captures of the motion repeat.
  const frozen =
    import.meta.env.DEV && typeof location !== "undefined" ? fieldClock(location.search) : null;
  const query =
    typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;
  let renderer: LampRenderer | null = createLampRenderer(canvas);
  let input: FieldInput = { mode: "watch", lights: [] };
  let reducedMotion = query?.matches ?? false;
  let grid: LampGrid | null = null;
  let cached: { theme: Theme; mode: FieldMode; config: FieldConfig; look: LampLook } | null = null;
  let frame = 0;
  let draws = 0;
  // A draw made on a Cytoscape render stands for this frame's, so the frame does not draw again.
  let fresh = false;

  // Frames are for lamps that get drawn: with no renderer there is nothing to animate.
  const animating = () =>
    renderer !== null &&
    !renderer.lost() &&
    shouldAnimate({
      reducedMotion,
      hidden: typeof document !== "undefined" && document.hidden,
      frozen: frozen !== null,
      lights: input.lights,
    });

  function settings() {
    const theme: Theme = getActiveTheme();
    if (!cached || cached.theme !== theme || cached.mode !== input.mode) {
      cached = {
        theme,
        mode: input.mode,
        config: fieldConfig(theme, input.mode),
        look: lampLook(theme),
      };
    }
    return { theme, config: cached.config, look: cached.look };
  }

  // A field that fails must not take the graph with it: Cytoscape calls this from its own render,
  // so the error is caught here, the renderer is dropped and the ground stays plain.
  function stop(error: unknown) {
    console.error("The dot field stopped drawing", error);
    renderer?.dispose();
    renderer = null;
    grid = null;
    schedule();
  }

  function draw(now: number) {
    if (!renderer || renderer.lost()) return;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (width === 0 || height === 0) return;
    try {
      const { theme, config, look } = settings();
      renderer.resize(width, height, window.devicePixelRatio || 1);
      const placed = placeLights(
        theme,
        input.lights,
        source.boxes(input.lights.map((light) => light.id)),
      );
      grid = lamps(config, width, height, placed, reducedMotion ? null : (frozen ?? now / 1000));
      renderer.draw(grid, look);
      draws += 1;
    } catch (error) {
      stop(error);
    }
  }

  function schedule() {
    if (animating()) {
      if (frame === 0) frame = requestAnimationFrame(tick);
    } else if (frame !== 0) {
      cancelAnimationFrame(frame);
      frame = 0;
    }
  }

  function tick(now: number) {
    frame = 0;
    if (!fresh) draw(now);
    fresh = false;
    schedule();
  }

  const redraw = () => {
    draw(performance.now());
  };
  const onGraph = () => {
    redraw();
    fresh = true;
  };
  const onMotion = () => {
    reducedMotion = query?.matches ?? false;
    schedule();
    redraw();
  };
  const onVisibility = () => {
    schedule();
    redraw();
  };
  const onLost = (event: Event) => {
    event.preventDefault();
  };
  const onRestored = () => {
    renderer = createLampRenderer(canvas);
    schedule();
    redraw();
  };

  const unsubscribe = source.subscribe(onGraph);
  const theme = new MutationObserver(redraw);
  theme.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  query?.addEventListener("change", onMotion);
  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("resize", redraw);
  canvas.addEventListener("webglcontextlost", onLost);
  canvas.addEventListener("webglcontextrestored", onRestored);

  canvas.oversightField = {
    state: () => (renderer && !renderer.lost() ? "webgl" : "plain"),
    animating: () => frame !== 0,
    draws: () => draws,
    lamp: (x, y) => {
      if (!grid) return null;
      const { cell } = settings().look;
      const col = Math.floor(x / cell);
      const row = Math.floor(y / cell);
      if (col < 0 || row < 0 || col >= grid.cols || row >= grid.rows) return null;
      const at = (row * grid.cols + col) * 4;
      const top = settings().config.levels - 1;
      return {
        level: Math.round(((grid.data[at + 3] ?? 0) / 255) * top),
        color: [grid.data[at] ?? 0, grid.data[at + 1] ?? 0, grid.data[at + 2] ?? 0],
      };
    },
    pixel: (x, y) => {
      redraw();
      return renderer?.pixel(x, y) ?? null;
    },
  };

  redraw();
  schedule();

  return {
    update(next) {
      if (next.mode === input.mode && sameLights(next.lights, input.lights)) return;
      input = next;
      schedule();
      redraw();
    },
    redraw,
    dispose() {
      if (frame !== 0) cancelAnimationFrame(frame);
      frame = 0;
      unsubscribe();
      theme.disconnect();
      query?.removeEventListener("change", onMotion);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("resize", redraw);
      canvas.removeEventListener("webglcontextlost", onLost);
      canvas.removeEventListener("webglcontextrestored", onRestored);
      renderer?.dispose();
      delete canvas.oversightField;
    },
  };
}
