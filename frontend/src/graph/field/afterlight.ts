// What a decided change leaves on the field. A merge sends a pulse from the agent's card to its
// node; either way the node's light keeps the level it waited at (so the hand-off from the waiting
// light is seamless), then fades out over about 90 seconds. Pure functions of a clock in
// milliseconds, so the controller only evaluates them and tests drive them with any times. Every
// number comes from a theme token.
import type { FieldLight, FieldMerge } from "./lights";
import { ease } from "./transition";

/** The theme values this reads; the full theme satisfies it. */
export interface AfterlightTheme {
  "waiting-light-weight": number;
  "afterlight-duration": string;
  "afterlight-curve": number;
  "merge-travel-delay": string;
  "merge-travel-duration": string;
  "merge-trail-bright": string;
  "merge-trail-faint": string;
  "merge-trail-fade": string;
  "merge-pulse-width": string;
  "transition-duration": string;
  "transition-easing": string;
}

export interface Afterlight {
  /** The light's weight before the mode's gain: the level a waiting change's light stands at. */
  weight: number;
  /** Milliseconds for the light to fade out. */
  duration: number;
  /** Exponent of the share left, (1 - p) to the curve. */
  curve: number;
  /** Milliseconds a merge waits before its pulse leaves the card, and the pulse takes to arrive. */
  delay: number;
  travel: number;
  /** Pixels of bright and of faint trail behind the head; milliseconds the pulse takes to fade. */
  bright: number;
  faint: number;
  trailFade: number;
  /** The pulse's line width in px. */
  width: number;
  /** Milliseconds the light takes to come up at the decision, as the waiting light goes out. */
  rise: number;
  easing: string;
}

const ms = Number.parseFloat;

export function afterlightOf(theme: AfterlightTheme): Afterlight {
  return {
    weight: theme["waiting-light-weight"],
    duration: ms(theme["afterlight-duration"]),
    curve: theme["afterlight-curve"],
    delay: ms(theme["merge-travel-delay"]),
    travel: ms(theme["merge-travel-duration"]),
    bright: ms(theme["merge-trail-bright"]),
    faint: ms(theme["merge-trail-faint"]),
    trailFade: ms(theme["merge-trail-fade"]),
    width: ms(theme["merge-pulse-width"]),
    rise: ms(theme["transition-duration"]),
    easing: theme["transition-easing"],
  };
}

/** The share of the light left at progress `p` of its fade: 1 at the start, 0 from the end. */
export function fadeShare(p: number, curve: number): number {
  return p >= 1 ? 0 : (1 - Math.max(0, p)) ** curve;
}

/** Slows the pulse's end: the head covers most of the route early and settles in. */
export const easeOut = (x: number) => 1 - (1 - Math.min(1, Math.max(0, x))) ** 3;

/** A pulse on its way: how far along its route the head is, and how much of it still shows. */
export interface Pulse {
  key: string;
  merge: FieldMerge;
  /** 0 at the card, 1 at the node. */
  progress: number;
  /** 1 while it travels, then falling to 0 as the trail fades. */
  opacity: number;
}

interface Running {
  merge: FieldMerge;
  start: number;
  /** Reduced motion: the light is there at once. */
  jump: boolean;
  /** Whether a pulse travels: a merge, and not under reduced motion. */
  pulse: boolean;
}

export class MergeLife {
  private readonly running = new Map<string, Running>();
  private readonly seen = new Set<string>();

  constructor(private look: Afterlight) {}

  setLook(look: Afterlight) {
    this.look = look;
  }

  /** Milliseconds from the decision until the pulse has arrived and the light starts to fade. */
  private arrival(run: Running): number {
    return run.pulse ? this.look.delay + this.look.travel : 0;
  }

  /**
   * Takes the merges the state reports. A key seen before is not started again, and one that
   * leaves the state is forgotten, so a replay that starts over starts them again. With `history`
   * (the state the field mounts on) every merge is only recorded: it happened before.
   */
  update(merges: readonly FieldMerge[], now: number, jump: boolean, history: boolean) {
    const keys = new Set(merges.map((merge) => merge.key));
    for (const key of [...this.seen])
      if (!keys.has(key)) {
        this.seen.delete(key);
        this.running.delete(key);
      }
    for (const merge of merges) {
      if (this.seen.has(merge.key)) continue;
      this.seen.add(merge.key);
      if (history) continue;
      const run = { merge, start: now - Math.max(0, merge.age), jump, pulse: merge.pulse && !jump };
      if (now < run.start + this.arrival(run) + this.look.duration)
        this.running.set(merge.key, run);
    }
  }

  /** The pulses to draw at `now`. */
  pulses(now: number): Pulse[] {
    const { delay, travel, trailFade } = this.look;
    const result: Pulse[] = [];
    for (const [key, run] of this.running) {
      if (!run.pulse) continue;
      const age = now - run.start;
      const progress = (age - delay) / travel;
      if (progress <= 0) continue;
      const opacity = progress < 1 ? 1 : 1 - (age - delay - travel) / trailFade;
      if (opacity > 0)
        result.push({ key, merge: run.merge, progress: Math.min(1, progress), opacity });
    }
    return result;
  }

  /**
   * The node lights at `now`: one per decided change whose fade is not over. The light comes up as
   * the waiting light goes out, holds its level while the pulse travels, then fades.
   */
  lights(now: number): FieldLight[] {
    const { weight, duration, curve, rise, easing } = this.look;
    const lights: FieldLight[] = [];
    for (const [key, run] of [...this.running]) {
      const since = now - run.start;
      const fading = since - this.arrival(run);
      if (fading >= duration) {
        this.running.delete(key);
        continue;
      }
      const coming = run.jump || rise <= 0 ? 1 : ease(Math.min(1, since / rise), easing);
      lights.push({
        id: run.merge.id,
        source: run.merge.source,
        weight: weight * coming * fadeShare(fading / duration, curve),
        pulses: false,
        steady: true,
      });
    }
    return lights;
  }

  /** True while a pulse is on its way or its trail has not faded: frames are worth drawing. */
  travelling(now: number): boolean {
    const end = this.look.delay + this.look.travel + this.look.trailFade;
    return [...this.running.values()].some((run) => run.pulse && now - run.start < end);
  }

  /** True while any merge has not finished: its light is on, or its pulse has not arrived. */
  active(now: number): boolean {
    return [...this.running.values()].some(
      (run) => now < run.start + this.arrival(run) + this.look.duration,
    );
  }
}
