// The dot field's life on the page: it listens to Cytoscape through the box source, draws when the
// graph or the lights change, and runs animation frames only while something breathes or a ring
// spreads, the tab is visible and motion is allowed. Where WebGL is missing it draws nothing, and
// the graph's background stays plain ground.
import { getActiveTheme } from "../../theme";

import type { BoxSource } from "./boxSource";
import { createLampField, fieldConfig, parseHex, placeLights, signalConfig } from "./model";
import { overlayBoxes, contains } from "./mask";
import { MarkerMotion } from "./marker";
import { MarkerLayer } from "./markerLayer";
import type { Box, LampGrid } from "./model";
import { fieldClock, shouldAnimate } from "./motion";
import { createLampRenderer } from "./renderer";
import type { LampLook, LampRenderer } from "./renderer";
import { FieldTransition } from "./transition";
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

/** All rendering and lifecycle operations are bounded methods; the public factory only mounts. */
export function createFieldController(canvas: FieldHost, source: BoxSource): FieldController {
  return new FieldRuntime(canvas, source);
}

class FieldRuntime implements FieldController {
  private readonly lamps = createLampField();
  private readonly markers = new MarkerMotion();
  private readonly markerLayer: MarkerLayer;
  private masks: Box[] = [];
  private masksDirty = true;
  private maskSize = "";
  private readonly overlayObserver?: MutationObserver;
  private readonly frozen =
    import.meta.env.DEV && typeof location !== "undefined" ? fieldClock(location.search) : null;
  private readonly query =
    typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;
  private renderer: LampRenderer | null;
  private readonly checkContext: LampRenderer["check"] | undefined;
  private input: FieldInput = { mode: "watch", lights: [] };
  private reducedMotion = this.query?.matches ?? false;
  private grid: LampGrid | null = null;
  private frame = 0;
  private draws = 0;
  private fresh = false;
  private theme = getActiveTheme();
  private look = lampLook(this.theme);
  private readonly transition = new FieldTransition({
    lights: [],
    config: fieldConfig(this.theme, "watch"),
  });
  private readonly unsubscribe: () => void;
  private readonly observer: MutationObserver;

  constructor(
    private readonly canvas: FieldHost,
    private readonly source: BoxSource,
  ) {
    this.markerLayer = new MarkerLayer(canvas);
    const stage =
      typeof canvas.closest === "function" ? canvas.closest('[aria-label="Graph stage"]') : null;
    if (stage) {
      this.overlayObserver = new MutationObserver((records) => {
        // Ignore our own paint and Cytoscape; their lifecycle already calls onGraph.
        if (records.some((record) => !canvas.parentElement?.contains(record.target))) {
          this.masksDirty = true;
          this.redraw();
        }
      });
      this.overlayObserver.observe(stage.closest(".shell-frame") ?? stage, {
        childList: true,
        characterData: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["class", "style", "hidden"],
      });
    }
    this.renderer = createLampRenderer(canvas);
    this.checkContext = this.renderer?.check;
    canvas.hidden = this.renderer === null;
    this.unsubscribe = source.subscribe(this.onGraph);
    this.observer = new MutationObserver(this.onTheme);
    this.observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    this.query?.addEventListener("change", this.onMotion);
    document.addEventListener("visibilitychange", this.onVisibility);
    window.addEventListener("resize", this.redraw);
    canvas.addEventListener("webglcontextlost", this.onContextChange);
    canvas.addEventListener("webglcontextrestored", this.onContextChange);
    canvas.oversightField = this.probe();
    this.redraw();
    this.schedule();
  }

  private animating() {
    return (
      this.renderer !== null &&
      !this.renderer.lost() &&
      !document.hidden &&
      !this.reducedMotion &&
      (this.transition.active(performance.now()) ||
        shouldAnimate({
          reducedMotion: false,
          hidden: false,
          frozen: this.frozen !== null,
          lights: this.input.lights,
        }))
    );
  }

  private stop() {
    try {
      this.checkContext?.();
    } catch {
      // Context errors are expected here; diagnostics must never prevent the plain fallback.
    }
    this.renderer?.dispose();
    this.renderer = null;
    this.grid = null;
    // Hide even a preserved/partially drawn buffer: the graph must show plain ground.
    this.canvas.hidden = true;
    this.markerLayer.dispose();
    this.schedule();
  }

  private draw(now: number) {
    if (this.renderer?.lost()) this.stop();
    if (!this.renderer) return;
    const { clientWidth: width, clientHeight: height } = this.canvas;
    if (width === 0 || height === 0) return;
    try {
      const { lights, config } = this.transition.sample(now);
      this.renderer.resize(width, height, Math.min(window.devicePixelRatio || 1, 1));
      const boxes = this.markers.boxes(lights, this.source, now);
      const placed = placeLights(this.theme, lights, boxes);
      const maskSize = `${String(width)}:${String(height)}`;
      if (this.masksDirty || this.maskSize !== maskSize) {
        this.masks = overlayBoxes(this.canvas);
        this.masksDirty = false;
        this.maskSize = maskSize;
      }
      this.grid = this.lamps(
        this.source.zoom ? signalConfig(config, this.source.zoom()) : config,
        width,
        height,
        placed,
        this.reducedMotion ? null : (this.frozen ?? now / 1000),
      );
      this.renderer.draw(this.grid, this.look, this.masks);
      this.markerLayer.draw(lights, boxes, this.masks, width, height);
      this.draws += 1;
    } catch (error) {
      console.error("The dot field stopped drawing", error);
      this.stop();
    }
  }

  private schedule() {
    if (this.animating()) {
      if (this.frame === 0) this.frame = requestAnimationFrame(this.tick);
    } else if (this.frame !== 0) {
      cancelAnimationFrame(this.frame);
      this.frame = 0;
    }
  }

  private tick = (now: number) => {
    this.frame = 0;
    if (!this.fresh || !this.animating()) this.draw(now);
    this.fresh = false;
    this.schedule();
  };

  redraw = () => {
    this.draw(performance.now());
  };
  private onGraph = () => {
    this.redraw();
    this.fresh = true;
  };
  private onMotion = () => {
    this.reducedMotion = this.query?.matches ?? false;
    if (this.reducedMotion) {
      this.transition.finish();
      this.markers.finish();
    }
    this.schedule();
    this.redraw();
  };
  private onVisibility = () => {
    this.redraw();
    this.schedule();
  };
  private onTheme = () => {
    this.theme = getActiveTheme();
    this.look = lampLook(this.theme);
    this.setTarget();
    this.redraw();
    this.schedule();
  };
  private onContextChange = () => {
    this.stop();
  };

  private setTarget() {
    this.markers.set(
      this.input.lights,
      this.source,
      performance.now(),
      this.theme["transition-duration"],
      this.theme["transition-easing"],
      this.reducedMotion,
    );
    this.transition.set(
      this.input,
      fieldConfig(this.theme, this.input.mode),
      performance.now(),
      this.theme["transition-duration"],
      this.theme["transition-easing"],
      this.reducedMotion,
    );
  }

  update(next: FieldInput) {
    if (next.mode === this.input.mode && sameLights(next.lights, this.input.lights)) return;
    this.input = next;
    this.setTarget();
    this.redraw();
    this.schedule();
  }

  private lamp(x: number, y: number): ReturnType<FieldProbe["lamp"]> {
    if (!this.grid) return null;
    if (contains(this.masks, x, y)) return { level: 0, color: [...parseHex(this.theme.ground)] };
    const col = Math.floor(x / this.look.cell);
    const row = Math.floor(y / this.look.cell);
    if (col < 0 || row < 0 || col >= this.grid.cols || row >= this.grid.rows) return null;
    const at = (row * this.grid.cols + col) * 4;
    const top = this.transition.sample(performance.now()).config.levels - 1;
    return {
      level: Math.round(((this.grid.data[at + 3] ?? 0) / 255) * top),
      color: [this.grid.data[at] ?? 0, this.grid.data[at + 1] ?? 0, this.grid.data[at + 2] ?? 0],
    };
  }

  private probe(): FieldProbe {
    return {
      state: () => (this.renderer && !this.renderer.lost() ? "webgl" : "plain"),
      animating: () => this.frame !== 0,
      draws: () => this.draws,
      lamp: (x, y) => this.lamp(x, y),
      pixel: (x, y) => {
        this.redraw();
        return this.renderer?.pixel(x, y) ?? null;
      },
    };
  }

  dispose() {
    if (this.frame !== 0) cancelAnimationFrame(this.frame);
    this.frame = 0;
    this.unsubscribe();
    this.observer.disconnect();
    this.overlayObserver?.disconnect();
    this.markerLayer.dispose();
    this.query?.removeEventListener("change", this.onMotion);
    document.removeEventListener("visibilitychange", this.onVisibility);
    window.removeEventListener("resize", this.redraw);
    this.canvas.removeEventListener("webglcontextlost", this.onContextChange);
    this.canvas.removeEventListener("webglcontextrestored", this.onContextChange);
    this.renderer?.dispose();
    delete this.canvas.oversightField;
  }
}
