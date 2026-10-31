// What a decided change leaves on the field. A decision fades only a light that exists: the node's
// light starts from the level the change had at that moment (working or waiting), so the hand-off
// is seamless, and fades out over about 90 seconds. A change with no light then gets none, except
// that a merge still sends its pulse from the agent's card to the node, and the node lights as the
// pulse arrives. Pure functions of a clock in milliseconds, so the controller only evaluates them
// and tests drive them with any times. Every number comes from a theme token.
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
  /** The weight a merge's light arrives with when the change had no light: a waiting change's. */
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

/** Shared arrival rule for field, card and pending marks. */
export const mergeArrivalDelay = (look: Afterlight, pulse = true) =>
  pulse ? look.delay + look.travel : 0;
export type Halo = { key: string; merge: FieldMerge; progress: number };

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
  /** The weight the light starts from: the change's own light at the decision, or the token's. */
  level: number;
  /** Milliseconds after the decision when the light comes up: 0 when it had one, else the arrival. */
  onset: number;
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
    return mergeArrivalDelay(this.look, run.pulse);
  }

  /**
   * Takes the merges the state reports. A key seen before is not started again, and one that
   * leaves the state is forgotten, so a replay that starts over starts them again. With `history`
   * (the state the field mounts on) every merge is only recorded: it happened before. `before` is
   * the lights the field showed just before: a decided change fades the light it had on its node,
   * and one that had none fades nothing (a merge still sends its pulse and lights its node as the
   * pulse arrives).
   */
  update(
    merges: readonly FieldMerge[],
    now: number,
    jump: boolean,
    history: boolean,
    before: readonly FieldLight[] = [],
    starts?: ReadonlyMap<string, number>,
  ) {
    const keys = new Set(merges.map((merge) => merge.key));
    for (const key of [...this.seen])
      if (!keys.has(key)) {
        this.seen.delete(key);
        this.running.delete(key);
      }
    for (const merge of merges) {
      if (this.seen.has(merge.key)) {
        const run = this.running.get(merge.key);
        const start = starts?.get(merge.key);
        if (run && start !== undefined) run.start = start;
        continue;
      }
      this.seen.add(merge.key);
      if (history) continue;
      const had = Math.max(
        0,
        ...before
          .filter((light) => light.id === merge.id && light.source === merge.source)
          .map((light) => light.weight),
      );
      // A rejection has no pulse, so it has nothing to show when its change had no light.
      if (had <= 0 && !merge.pulse) continue;
      const run: Running = {
        merge,
        start: starts?.get(merge.key) ?? now - Math.max(0, merge.age),
        level: had > 0 ? had : this.look.weight,
        onset: 0,
        jump,
        pulse: merge.pulse && !jump,
      };
      if (had <= 0) run.onset = this.arrival(run);
      if (now < run.start + this.arrival(run) + this.look.duration)
        this.running.set(merge.key, run);
    }
  }

  finishMotion() {
    for (const run of this.running.values()) {
      run.pulse = false;
      run.jump = true;
      run.onset = 0;
    }
  }

  halos(now: number, duration: number): Halo[] {
    return [...this.running.values()].flatMap((run) => {
      const age = now - run.start - this.arrival(run);
      return run.pulse && age >= 0 && age < duration
        ? [{ key: run.merge.key, merge: run.merge, progress: age / duration }]
        : [];
    });
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
   * the light the change had goes out, holds its level while the pulse travels, then fades. One
   * that had none comes up as the pulse arrives.
   */
  lights(now: number): FieldLight[] {
    const { duration, curve, rise, easing } = this.look;
    const lights: FieldLight[] = [];
    for (const [key, run] of [...this.running]) {
      const since = now - run.start;
      const fading = since - this.arrival(run);
      if (fading >= duration) {
        this.running.delete(key);
        continue;
      }
      if (since < run.onset) continue;
      const coming =
        run.jump || rise <= 0 ? 1 : ease(Math.min(1, (since - run.onset) / rise), easing);
      lights.push({
        id: run.merge.id,
        source: run.merge.source,
        weight: run.level * coming * fadeShare(fading / duration, curve),
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
