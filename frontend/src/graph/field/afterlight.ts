// What a merge leaves on the field: a pulse that runs from the agent's card to its node, then the
// node's light, which fades out over about 90 seconds. Pure functions of a clock in milliseconds,
// so the controller only evaluates them and tests drive them with any times. Every number comes
// from a theme token.
import type { FieldLight, FieldMerge } from "./lights";

/** The theme values this reads; the full theme satisfies it. */
export interface AfterlightTheme {
  "afterlight-weight": number;
  "afterlight-duration": string;
  "afterlight-curve": number;
  "merge-travel-delay": string;
  "merge-travel-duration": string;
  "merge-trail-bright": string;
  "merge-trail-faint": string;
  "merge-trail-fade": string;
  "transition-duration": string;
}

export interface Afterlight {
  /** The light's weight when its pulse arrives, before the mode's gain. */
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
  /** Milliseconds the light takes to come up when the pulse arrives. */
  rise: number;
}

const ms = Number.parseFloat;

export function afterlightOf(theme: AfterlightTheme): Afterlight {
  return {
    weight: theme["afterlight-weight"],
    duration: ms(theme["afterlight-duration"]),
    curve: theme["afterlight-curve"],
    delay: ms(theme["merge-travel-delay"]),
    travel: ms(theme["merge-travel-duration"]),
    bright: ms(theme["merge-trail-bright"]),
    faint: ms(theme["merge-trail-faint"]),
    trailFade: ms(theme["merge-trail-fade"]),
    rise: ms(theme["transition-duration"]),
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
  /** Reduced motion: no pulse, the light is there at once. */
  jump: boolean;
}

export class MergeLife {
  private readonly running = new Map<string, Running>();
  private readonly seen = new Set<string>();

  constructor(private look: Afterlight) {}

  setLook(look: Afterlight) {
    this.look = look;
  }

  /** Milliseconds from a merge's start until its pulse has arrived. */
  private arrival(run: Running): number {
    return run.jump ? 0 : this.look.delay + this.look.travel;
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
      const run = { merge, start: now - Math.max(0, merge.age), jump };
      if (now < run.start + this.arrival(run) + this.look.duration)
        this.running.set(merge.key, run);
    }
  }

  /** The pulses to draw at `now`. */
  pulses(now: number): Pulse[] {
    const { delay, travel, trailFade } = this.look;
    const result: Pulse[] = [];
    for (const [key, run] of this.running) {
      if (run.jump) continue;
      const age = now - run.start;
      const progress = (age - delay) / travel;
      if (progress <= 0) continue;
      const opacity = progress < 1 ? 1 : 1 - (age - delay - travel) / trailFade;
      if (opacity > 0)
        result.push({ key, merge: run.merge, progress: Math.min(1, progress), opacity });
    }
    return result;
  }

  /** The node lights at `now`: one per merge whose pulse has arrived and whose fade is not over. */
  lights(now: number): FieldLight[] {
    const { weight, duration, curve, rise } = this.look;
    const lights: FieldLight[] = [];
    for (const [key, run] of [...this.running]) {
      const since = now - run.start - this.arrival(run);
      if (since < 0) continue;
      if (since >= duration) {
        this.running.delete(key);
        continue;
      }
      const coming = run.jump || rise <= 0 ? 1 : Math.min(1, since / rise);
      lights.push({
        id: run.merge.id,
        source: run.merge.source,
        weight: weight * coming * fadeShare(since / duration, curve),
        pulses: false,
        steady: true,
      });
    }
    return lights;
  }

  /** True while a pulse is on its way or its trail has not faded: frames are worth drawing. */
  travelling(now: number): boolean {
    const end = this.look.delay + this.look.travel + this.look.trailFade;
    return [...this.running.values()].some((run) => !run.jump && now - run.start < end);
  }

  /** True while any merge has not finished: its light is on, or its pulse has not arrived. */
  active(now: number): boolean {
    return [...this.running.values()].some(
      (run) => now < run.start + this.arrival(run) + this.look.duration,
    );
  }
}
