// One finite event on the existing lamp field, independent of the eight persistent emitters.
import type { LightSource, FieldMerge } from "./lights";
import type { PulseLight } from "./pulseLights";
import {
  afterlightOf,
  mergeArrivalDelay,
  MergeLife as AfterlightLife,
  type Afterlight,
} from "./afterlight";
import type { FieldLight } from "./lights";
import type { Box } from "./model";
import type { getActiveTheme } from "../../theme";
type Theme = ReturnType<typeof getActiveTheme>;
export { mergeArrivalDelay };
export type Halo = { key: string; merge: FieldMerge; progress: number };

export interface Milestone extends Omit<FieldMerge, "pulse"> {
  kind: "ready" | "needs" | "merged";
}
export interface RingLight extends PulseLight {
  sigma: number;
  tail: number;
  inward: boolean;
}
interface Run {
  event: Milestone;
  start: number;
  end: number;
}
const priority = { ready: 0, merged: 1, needs: 2 };
/** Radius uses the rendered row width, including folded rows; never starts inside the row. */
export function milestoneRing(event: Milestone, box: Box, age: number, theme: Theme): RingLight {
  const needs = event.kind === "needs";
  const duration = theme[needs ? "milestone-needs-duration" : "milestone-ready-duration"];
  const beats = needs ? theme["milestone-needs-beats"] : 1;
  const beat = duration / beats;
  const local = age >= duration || (age > 0 && age % beat === 0) ? beat : Math.max(0, age) % beat;
  const p = Math.min(1, local / beat);
  const distance = theme["milestone-distance"];
  const radius =
    (box.x2 - box.x1) / 2 +
    distance *
      (needs
        ? 1 - p ** theme["milestone-needs-curve"]
        : 1 - (1 - p) ** theme["milestone-ready-curve"]);
  const intensity = needs
    ? theme["milestone-needs-amplitude"] *
      (theme["milestone-needs-min"] +
        (1 - theme["milestone-needs-min"]) * p ** theme["milestone-needs-rise"]) *
      Math.min(1, local / theme["milestone-needs-fade-in"]) *
      Math.max(0, 1 - Math.max(0, age - duration) / theme["milestone-needs-afterglow"])
    : theme["milestone-ready-amplitude"] * (1 - p) ** theme["milestone-ready-decay"];
  return {
    x: (box.x1 + box.x2) / 2,
    y: (box.y1 + box.y2) / 2,
    radius,
    sigma: theme[needs ? "milestone-needs-sigma" : "milestone-ready-sigma"],
    tail: theme[needs ? "milestone-needs-tail" : "milestone-ready-tail"],
    intensity,
    inward: needs,
    source: event.source,
  };
}
/** History never runs; removing keys forgets them so replay can start again. */
export class MilestoneLife {
  private seen = new Set<string>();
  private runs = new Map<string, Run>();
  constructor(private theme: Theme) {}
  setLook(theme: Theme) {
    this.theme = theme;
  }
  update(events: readonly Milestone[], now: number, reduced: boolean, history: boolean) {
    const keys = new Set(events.map((event) => event.key));
    for (const key of this.seen)
      if (!keys.has(key)) {
        this.seen.delete(key);
        this.runs.delete(key);
      }
    for (const event of [...events].sort((a, b) => b.age - a.age)) {
      if (this.seen.has(event.key)) continue;
      this.seen.add(event.key);
      if (history || reduced) continue;
      const start = now - event.age;
      const same = [...this.runs.values()].filter(
        (run) =>
          run.event.source === event.source &&
          Math.abs(start - run.start) < this.theme["milestone-coalesce"],
      );
      if (
        event.kind !== "merged" &&
        same.some((run) => priority[run.event.kind] >= priority[event.kind])
      )
        continue;
      for (const run of same) this.runs.delete(run.event.key);
      const duration =
        event.kind === "merged"
          ? mergeArrivalDelay(afterlightOf(this.theme)) +
            Number.parseFloat(this.theme["merge-trail-fade"])
          : this.theme[
              event.kind === "needs" ? "milestone-needs-duration" : "milestone-ready-duration"
            ] + (event.kind === "needs" ? this.theme["milestone-needs-afterglow"] : 0);
      let scheduled = start;
      const other = [...this.runs.values()].filter(
        (run) =>
          event.kind !== "merged" &&
          run.event.kind !== "merged" &&
          run.event.id === event.id &&
          run.event.source !== event.source &&
          run.end > start,
      );
      for (const run of other)
        scheduled = Math.max(scheduled, run.start + this.theme["milestone-collision-delay"]);
      let active = [...this.runs.values()].filter((run) => run.end > scheduled);
      if (active.length >= this.theme["milestone-limit"]) {
        const victim = active.sort((a, b) => priority[a.event.kind] - priority[b.event.kind])[0];
        if (
          victim &&
          victim.event.kind !== "needs" &&
          priority[victim.event.kind] <= priority[event.kind]
        )
          this.runs.delete(victim.event.key);
        else if (event.kind !== "needs") continue;
        else {
          // A fifth Needs you is deferred, never dropped or allowed to exceed the simultaneous cap.
          while (active.length >= this.theme["milestone-limit"]) {
            scheduled = Math.min(...active.map((run) => run.end));
            active = active.filter((run) => run.end > scheduled);
          }
        }
      }
      this.runs.set(event.key, { event, start: scheduled, end: scheduled + duration });
    }
  }
  finishMotion() {
    this.runs.clear();
  }
  accepted(key: string) {
    return this.runs.has(key);
  }
  visible(key: string, now: number) {
    const run = this.runs.get(key);
    return !!run && now >= run.start && now < run.end;
  }
  rings(now: number, boxes: ReadonlyMap<string, Box>): RingLight[] {
    return [...this.runs.values()].flatMap((run) => {
      const box = boxes.get(run.event.id);
      return box && run.event.kind !== "merged" && this.visible(run.event.key, now)
        ? [milestoneRing(run.event, box, now - run.start, this.theme)]
        : [];
    });
  }
  gain(source: LightSource, now: number): number {
    const run = [...this.runs.values()].findLast(
      (run) =>
        run.event.source === source &&
        run.event.kind !== "merged" &&
        this.visible(run.event.key, now),
    );
    if (!run) return 1;
    const age = now - run.start;
    if (run.event.kind === "ready")
      return (
        1 -
        this.theme["milestone-ready-dip"] *
          Math.max(0, 1 - age / this.theme["milestone-ready-dip-duration"])
      );
    const landing = age - this.theme["milestone-needs-duration"];
    return landing < 0
      ? 1 - this.theme["milestone-needs-dip"]
      : 1 +
          this.theme["milestone-needs-swell"] *
            (1 - landing / this.theme["milestone-needs-afterglow"]);
  }
  active(now: number) {
    return [...this.runs.values()].some((run) => now < run.end);
  }
}

// Keep the r6 afterlight untouched: each merge runs it on the shared arrival clock.
interface ArrivalRun {
  merge: FieldMerge;
  start: number;
  level: number;
  hadLight: boolean;
  pulse: boolean;
  life: AfterlightLife;
}
export class MergeLife {
  private readonly running = new Map<string, ArrivalRun>();
  private readonly seen = new Set<string>();
  constructor(private look: Afterlight) {}
  private prune(now: number) {
    for (const [key, run] of this.running)
      if (
        now >=
        run.start +
          mergeArrivalDelay(this.look, run.pulse) +
          Math.max(this.look.duration, this.look.trailFade)
      )
        this.running.delete(key);
  }
  setLook(look: Afterlight) {
    this.look = look;
    for (const run of this.running.values()) run.life.setLook(look);
  }
  private install(run: ArrivalRun, jump: boolean) {
    run.life = new AfterlightLife(this.look);
    run.life.update(
      [{ ...run.merge, age: 0, pulse: run.pulse }],
      0,
      jump,
      false,
      run.hadLight || !run.pulse
        ? [
            {
              id: run.merge.id,
              source: run.merge.source,
              weight: run.level,
              pulses: false,
            },
          ]
        : [],
    );
  }
  update(
    merges: readonly FieldMerge[],
    now: number,
    jump: boolean,
    history: boolean,
    before: readonly FieldLight[] = [],
    starts?: ReadonlyMap<string, number>,
  ) {
    this.prune(now);
    const keys = new Set(merges.map((merge) => merge.key));
    for (const key of this.seen)
      if (!keys.has(key)) {
        this.seen.delete(key);
        this.running.delete(key);
      }
    for (const merge of merges) {
      const start = starts?.get(merge.key);
      const existing = this.running.get(merge.key);
      if (this.seen.has(merge.key)) {
        if (existing && start !== undefined) {
          if (Number.isFinite(start)) existing.start = start;
          else if (existing.pulse) {
            existing.pulse = false;
            this.install(existing, true);
          }
        }
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
      if (had <= 0 && !merge.pulse) continue;
      const run: ArrivalRun = {
        merge,
        start: start !== undefined && Number.isFinite(start) ? start : now - Math.max(0, merge.age),
        level: had > 0 ? had : this.look.weight,
        hadLight: had > 0,
        pulse: merge.pulse && !jump && start !== -Infinity,
        life: new AfterlightLife(this.look),
      };
      this.install(run, jump || start === -Infinity);
      this.running.set(merge.key, run);
    }
  }
  finishMotion() {
    for (const run of this.running.values()) {
      run.pulse = false;
      this.install(run, true);
    }
  }
  halos(now: number, duration: number): Halo[] {
    this.prune(now);
    return [...this.running.values()].flatMap((run) => {
      const age = now - run.start - mergeArrivalDelay(this.look, run.pulse);
      return run.pulse && age >= 0 && age < duration
        ? [{ key: run.merge.key, merge: run.merge, progress: age / duration }]
        : [];
    });
  }
  pulses(now: number) {
    this.prune(now);
    return [...this.running.values()].flatMap((run) => run.life.pulses(now - run.start));
  }
  lights(now: number) {
    this.prune(now);
    return [...this.running.values()].flatMap((run) => run.life.lights(now - run.start));
  }
  travelling(now: number) {
    this.prune(now);
    return [...this.running.values()].some((run) => run.life.travelling(now - run.start));
  }
  active(now: number) {
    this.prune(now);
    return [...this.running.values()].some((run) => run.life.active(now - run.start));
  }
}
