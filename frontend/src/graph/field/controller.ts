// The dot field's life on the page: it listens to Cytoscape through the box source, draws when the
// graph or the lights change, and runs animation frames only while something breathes, a ring
// spreads or a merge's pulse travels, the tab is visible and motion is allowed. What changes too
// slowly for frames, the rest contours' drift and a merge's fading light, repaints on a slow timer.
// Where WebGL is missing it draws nothing, and the graph's background stays plain ground.
import { getActiveTheme } from "../../theme";
import type { XY } from "../orthogonal";

import { MilestoneLife, MergeLife, type Milestone } from "./milestones";
import { afterlightOf } from "./afterlight";
import type { Afterlight } from "./afterlight";
import type { BoxSource } from "./boxSource";
import { createLampField, fieldConfig, parseHex, placeLights, signalConfig } from "./model";
import { overlayBoxes, contains } from "./mask";
import { MarkerMotion } from "./marker";
import { MarkerLayer } from "./markerLayer";
import type { Box, LampGrid } from "./model";
import { fieldClock, shouldAnimate } from "./motion";
import { createLampRenderer } from "./renderer";
import type { LampLook, LampRenderer } from "./renderer";
import {
  contourLook,
  coverHeights,
  createHeightField,
  hillsOf,
  moundsOf,
  terrainConfig,
} from "./terrain";
import type { Contours, Heights, Hill, TerrainConfig, TerrainModule } from "./terrain";
import { pulseLights } from "./pulseLights";
import { approachPath, TravelLayer } from "./travel";
import { FieldTransition } from "./transition";
import { sameLights } from "./lights";
import type { FieldLight, FieldMerge, FieldMode, FieldModule } from "./lights";

/**
 * What the field is told: the mode's light profile and the lights to draw; the merges, whose pulse
 * and fading light it runs from the time it learns of them; and the modules the rest contours
 * rise over. Without `modules` there are no contours, without `merges` no merge travel.
 */
export interface FieldInput {
  mode: FieldMode;
  lights: readonly FieldLight[];
  merges?: readonly FieldMerge[];
  milestones?: readonly Milestone[];
  mergeStarts?: ReadonlyMap<string, number>;
  modules?: readonly FieldModule[];
}

const sameMerges = (a: readonly FieldMerge[] = [], b: readonly FieldMerge[] = []) =>
  a.length === b.length &&
  a.every((merge, i) => merge.key === b.at(i)?.key && merge.id === b.at(i)?.id);

const sameModules = (a?: readonly FieldModule[], b?: readonly FieldModule[]) =>
  a === b ||
  (a !== undefined &&
    b !== undefined &&
    a.length === b.length &&
    a.every((module, i) => module.id === b.at(i)?.id && module.callers === b.at(i)?.callers));

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

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
  /** The merges' pulses on the page and node lights burning now, and whether contours are drawn. */
  life(): { pulses: number; lights: number; contours: boolean };
  /** The lamp of the cell at a point, in CSS px from the graph's top left, as last computed. */
  lamp(x: number, y: number): { level: number; color: [number, number, number] } | null;
  /** The boxes that hid the lamps at the last draw: overlays and frame headers, in CSS px. */
  masks(): Box[];
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
  /** The overlay masks and the frame headers at the last draw: what hides lamps and lines only. */
  private drawnMasks: Box[] = [];
  private masksDirty = true;
  private maskSize = "";
  private readonly overlayObserver?: MutationObserver;
  private layoutHost?: Element;
  private disposed = false;
  private readonly frozen =
    import.meta.env.DEV && typeof location !== "undefined" ? fieldClock(location.search) : null;
  private readonly query =
    typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;
  private renderer: LampRenderer | null;
  private readonly travelLayer: TravelLayer;
  private readonly checkContext: LampRenderer["check"] | undefined;
  private input: FieldInput = { mode: "watch", lights: [] };
  private reducedMotion = this.query?.matches ?? false;
  private grid: LampGrid | null = null;
  private frame = 0;
  private draws = 0;
  private fresh = false;
  private paintedAt = 0;
  private theme = getActiveTheme();
  private look = lampLook(this.theme);
  private afterlight: Afterlight = afterlightOf(this.theme);
  private readonly life = new MergeLife(this.afterlight);
  private readonly milestones = new MilestoneLife(this.theme);
  private primed = false;
  private terrain: TerrainConfig = terrainConfig(this.theme);
  private readonly heightField = createHeightField();
  private hills: Hill[] = [];
  private hillsKey = "";
  private heights: Heights | null = null;
  private heightsBucket = Number.NaN;
  private contoursShown = false;
  private slowTimer: ReturnType<typeof setTimeout> | undefined;
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
    this.travelLayer = new TravelLayer(canvas);
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
      this.layoutHost = stage.closest(".shell-frame") ?? stage;
      this.overlayObserver.observe(this.layoutHost, {
        childList: true,
        characterData: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["class", "style", "hidden"],
      });
      // An overlay can change size with no change to the page's markup: a font arrives, or a panel
      // finishes growing. The masks are measured again, so no line shows under its new edge.
      this.layoutHost.addEventListener("transitionend", this.onLayout);
      if ("fonts" in document) void document.fonts.ready.then(this.onLayout);
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
      this.canvas.clientWidth > 0 &&
      this.canvas.clientHeight > 0 &&
      this.renderer !== null &&
      !this.renderer.lost() &&
      !document.hidden &&
      !this.reducedMotion &&
      // A draw can cross the deadline: keep a frame until the final scene has been painted.
      (this.transition.active(this.paintedAt) ||
        this.life.travelling(performance.now()) ||
        this.life.halos(performance.now(), this.theme["merge-halo-duration"]).length > 0 ||
        this.milestones.active(performance.now()) ||
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
    this.travelLayer.dispose();
    this.schedule();
  }

  private draw(now: number) {
    if (this.renderer?.lost()) this.stop();
    if (!this.renderer) return;
    const { clientWidth: width, clientHeight: height } = this.canvas;
    if (width === 0 || height === 0) {
      // A hidden canvas has no final scene to paint; let its transition reach the stop condition.
      this.paintedAt = now;
      return;
    }
    try {
      const sampled = this.transition.sample(now);
      const { config } = sampled;
      const burning = this.life.lights(now);
      const lights = burning.length > 0 ? [...sampled.lights, ...burning] : sampled.lights;
      this.renderer.resize(width, height, Math.min(window.devicePixelRatio || 1, 1));
      const boxes = this.markers.boxes(lights, this.source, now);
      const placed = placeLights(
        this.theme,
        lights.map((light) => ({
          ...light,
          weight: light.weight * this.milestones.gain(light.source, now),
          pulseWeight: (light.pulseWeight ?? 1) * this.milestones.gain(light.source, now),
        })),
        boxes,
      );
      const rings = this.reducedMotion
        ? []
        : this.milestones.rings(
            now,
            this.source.boxes((this.input.milestones ?? []).map((event) => event.id)),
          );
      const maskSize = `${String(width)}:${String(height)}`;
      if (this.masksDirty || this.maskSize !== maskSize) {
        this.masks = overlayBoxes(this.canvas);
        this.masksDirty = false;
        this.maskSize = maskSize;
      }
      // Frame headers move with the camera, so they are read on every draw; overlays only when dirty.
      const masks = this.source.headers ? [...this.masks, ...this.source.headers()] : this.masks;
      this.drawnMasks = masks;
      const time = this.reducedMotion ? null : (this.frozen ?? now / 1000);
      const points = this.drawPulses(now, width, height);
      this.grid = this.lamps(
        this.source.zoom ? signalConfig(config, this.source.zoom()) : config,
        width,
        height,
        placed,
        time,
        points,
        rings,
      );
      const contours = this.contours(this.grid, width, height, time);
      this.renderer.draw(this.grid, this.look, masks, contours);
      // A marker is a letter above the graph, as the sketch's DOM marker is: only the overlays clip it,
      // never a frame header or a folded card it sits on.
      this.markerLayer.draw(lights, boxes, this.masks, width, height);
      this.paintedAt = now;
      this.draws += 1;
    } catch (error) {
      console.error("The dot field stopped drawing", error);
      this.stop();
    }
  }

  /**
   * The rest ground for this draw: hills where the modules' frames are and mounds in the empty
   * areas, their heights recomputed when the layout changes or the slow clock moves on, and the
   * light's cover set every time. Undefined without modules in the input, and without a clock the
   * ground stands still.
   */
  private contours(
    grid: LampGrid,
    width: number,
    height: number,
    time: number | null,
  ): Contours | undefined {
    const modules = this.input.modules;
    this.contoursShown = modules !== undefined;
    if (!modules) return undefined;
    const frames =
      this.source.frames?.(modules.map((module) => module.id)) ?? new Map<string, Box>();
    const placed = modules.flatMap((module): TerrainModule[] => {
      const frame = frames.get(module.id);
      return frame ? [{ ...module, frame }] : [];
    });
    const key = [
      width,
      height,
      this.terrain.cell,
      ...placed.map(({ id, callers, frame }) =>
        [id, callers, ...[frame.x1, frame.y1, frame.x2, frame.y2].map(Math.round)].join(":"),
      ),
    ].join("|");
    if (key !== this.hillsKey) {
      this.hillsKey = key;
      this.hills = [
        ...hillsOf(this.terrain, placed),
        ...moundsOf(
          this.terrain,
          placed.map((module) => module.frame),
          width,
          height,
        ),
      ];
      this.heights = null;
    }
    const bucket = time === null ? 0 : Math.floor((time * 1000) / this.slowInterval());
    if (!this.heights || bucket !== this.heightsBucket) {
      this.heights = this.heightField(this.terrain, this.hills, width, height, time);
      this.heightsBucket = bucket;
    }
    coverHeights(this.heights, grid, this.look.cell);
    return { heights: this.heights, look: contourLook(this.terrain) };
  }

  /** Draws each pulse on its way from the agent's card to its node, never under reduced motion. */
  private drawPulses(now: number, width: number, height: number) {
    const pulses = this.reducedMotion
      ? []
      : this.life
          .pulses(now)
          .filter((pulse) => !this.input.milestones || this.milestones.visible(pulse.key, now));
    const paths = new Map<string, XY[]>();
    if (pulses.length > 0) {
      const origin = this.canvas.getBoundingClientRect();
      const targets = this.source.boxes(pulses.map((pulse) => pulse.merge.id));
      for (const pulse of pulses) {
        const target = targets.get(pulse.merge.id);
        if (!target) continue;
        const agent = [
          ...document.querySelectorAll<HTMLElement>(`[data-agent-origin="${pulse.merge.slot}"]`),
        ]
          .map((element) => ({ element, rect: element.getBoundingClientRect() }))
          .find(({ rect }) => rect.width > 0);
        const start = agent
          ? {
              x: agent.rect.left - origin.left,
              y: agent.rect.top + agent.rect.height / 2 - origin.top,
            }
          : { x: width, y: (target.y1 + target.y2) / 2 };
        // AgentCard carries data-slot; the folded rail letter deliberately omits it.
        // Both expose data-agent-origin: clamp the card, depart from the actual rail letter.
        if (agent?.element.hasAttribute("data-slot")) {
          start.x = clamp(start.x, 0, width);
          start.y = clamp(start.y, 0, height);
        }
        paths.set(
          pulse.key,
          approachPath(start, target, this.source.edgesAt?.(pulse.merge.id) ?? []),
        );
      }
    }
    this.travelLayer.draw(pulses, paths, this.masks, width, height, this.afterlight);
    this.travelLayer.halos(
      this.reducedMotion
        ? []
        : this.life
            .halos(now, this.theme["merge-halo-duration"])
            .filter((halo) => !this.input.milestones || this.milestones.accepted(halo.key)),
      this.source.boxes((this.input.merges ?? []).map((merge) => merge.id)),
      this.masks,
      width,
      height,
    );
    return pulseLights(pulses, paths, this.theme);
  }

  private slowInterval() {
    return Number.parseFloat(this.theme["field-slow-interval"]);
  }

  /** The slow timer runs while the contours drift or a merge's light fades, and only then. */
  private slowWanted() {
    return (
      this.renderer !== null &&
      !document.hidden &&
      ((this.input.modules !== undefined && !this.reducedMotion) ||
        this.life.active(performance.now()))
    );
  }

  private scheduleSlow() {
    if (this.slowWanted()) {
      this.slowTimer ??= setTimeout(this.slowTick, this.slowInterval());
    } else if (this.slowTimer !== undefined) {
      clearTimeout(this.slowTimer);
      this.slowTimer = undefined;
    }
  }

  private slowTick = () => {
    this.slowTimer = undefined;
    // Frames, while they run, draw everything; the timer only fills the quiet between events.
    if (this.frame === 0) this.draw(performance.now());
    this.scheduleSlow();
  };

  private schedule() {
    if (this.animating()) {
      if (this.frame === 0) this.frame = requestAnimationFrame(this.tick);
    } else if (this.frame !== 0) {
      cancelAnimationFrame(this.frame);
      this.frame = 0;
    }
    this.scheduleSlow();
  }

  private tick = (now: number) => {
    this.frame = 0;
    if (!this.fresh || !this.animating()) this.draw(now);
    this.fresh = false;
    this.schedule();
  };

  redraw = () => {
    this.draw(performance.now());
    this.schedule();
  };
  private onGraph = () => {
    this.redraw();
    this.fresh = true;
  };
  private onMotion = () => {
    this.reducedMotion = this.query?.matches ?? false;
    if (this.reducedMotion) {
      this.life.finishMotion();
      this.milestones.finishMotion();
      this.transition.finish();
      this.markers.finish();
    }
    this.schedule();
    this.redraw();
  };
  private onLayout = () => {
    if (this.disposed) return;
    this.masksDirty = true;
    this.redraw();
  };
  private onVisibility = () => {
    this.redraw();
    this.schedule();
  };
  private onTheme = () => {
    this.theme = getActiveTheme();
    this.look = lampLook(this.theme);
    this.afterlight = afterlightOf(this.theme);
    this.life.setLook(this.afterlight);
    this.milestones.setLook(this.theme);
    this.terrain = terrainConfig(this.theme);
    this.hillsKey = "";
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
    // The first input is the state the field mounts on: its merges happened before.
    const history = !this.primed;
    this.primed = true;
    if (
      next.mode === this.input.mode &&
      sameLights(next.lights, this.input.lights) &&
      sameMerges(next.merges, this.input.merges) &&
      sameMerges(
        next.milestones?.map((event) => ({ ...event, pulse: false })),
        this.input.milestones?.map((event) => ({ ...event, pulse: false })),
      ) &&
      next.mergeStarts === this.input.mergeStarts &&
      sameModules(next.modules, this.input.modules)
    )
      return;
    this.milestones.update(next.milestones ?? [], performance.now(), this.reducedMotion, history);
    if (next.merges)
      this.life.update(
        next.merges,
        performance.now(),
        this.reducedMotion,
        history,
        this.input.lights,
        next.mergeStarts,
      );
    this.input = next;
    this.setTarget();
    this.redraw();
    this.schedule();
  }

  private lamp(x: number, y: number): ReturnType<FieldProbe["lamp"]> {
    if (!this.grid) return null;
    if (contains(this.drawnMasks, x, y))
      return { level: 0, color: [...parseHex(this.theme.ground)] };
    const col = Math.floor(x / this.look.cell);
    const row = Math.floor(y / this.look.cell);
    if (col < 0 || row < 0 || col >= this.grid.cols || row >= this.grid.rows) return null;
    const at = (row * this.grid.cols + col) * 4;
    return {
      // The probe reads a lamp's continuous brightness in thirds, 0 off and 3 whole.
      level: Math.round(((this.grid.data[at + 3] ?? 0) / 255) * 3),
      color: [this.grid.data[at] ?? 0, this.grid.data[at + 1] ?? 0, this.grid.data[at + 2] ?? 0],
    };
  }

  private probe(): FieldProbe {
    return {
      state: () => (this.renderer && !this.renderer.lost() ? "webgl" : "plain"),
      animating: () => this.frame !== 0,
      draws: () => this.draws,
      life: () => ({
        pulses: this.reducedMotion ? 0 : this.life.pulses(performance.now()).length,
        lights: this.life.lights(performance.now()).length,
        contours: this.renderer !== null && this.contoursShown,
      }),
      lamp: (x, y) => this.lamp(x, y),
      masks: () => this.drawnMasks.map((box) => ({ ...box })),
      pixel: (x, y) => {
        this.redraw();
        return this.renderer?.pixel(x, y) ?? null;
      },
    };
  }

  dispose() {
    this.disposed = true;
    this.layoutHost?.removeEventListener("transitionend", this.onLayout);
    if (this.frame !== 0) cancelAnimationFrame(this.frame);
    this.frame = 0;
    if (this.slowTimer !== undefined) clearTimeout(this.slowTimer);
    this.slowTimer = undefined;
    this.unsubscribe();
    this.observer.disconnect();
    this.overlayObserver?.disconnect();
    this.markerLayer.dispose();
    this.travelLayer.dispose();
    this.query?.removeEventListener("change", this.onMotion);
    document.removeEventListener("visibilitychange", this.onVisibility);
    window.removeEventListener("resize", this.redraw);
    this.canvas.removeEventListener("webglcontextlost", this.onContextChange);
    this.canvas.removeEventListener("webglcontextrestored", this.onContextChange);
    this.renderer?.dispose();
    delete this.canvas.oversightField;
  }
}
