// One presentation clock shared by the field, card and pending marks; never reducer state.
import { useLayoutEffect, useState, useSyncExternalStore } from "react";
// eslint-disable-next-line no-restricted-imports -- C1 uses a pure leaf to isolate graph startup.
import { afterlightOf } from "../graph/field/afterlight";
// eslint-disable-next-line no-restricted-imports -- C1 uses a pure leaf to isolate graph startup.
import { mergeArrivalDelay, MilestoneLife } from "../graph/field/milestones";
// eslint-disable-next-line no-restricted-imports -- C1 uses a pure leaf to isolate graph startup.
import { selectMerges, selectMilestones } from "../graph/field/life";
import type { State } from "../state";
import { getActiveTheme } from "../theme";
type Starts = ReadonlyMap<string, number>;
const changeId = (key: string) => key.slice(0, key.lastIndexOf(":"));
const arrivalDelay = () => mergeArrivalDelay(afterlightOf(getActiveTheme()));
const sameMap = <T>(a: ReadonlyMap<string, T>, b: ReadonlyMap<string, T>) =>
  a.size === b.size && [...a].every(([key, value]) => b.get(key) === value);
export function mergeArrivals(
  state: State,
  base: Starts,
  now: number,
  history = false,
  accepted: (key: string) => boolean = () => true,
) {
  const starts = new Map<string, number>();
  for (const merge of selectMerges(state))
    if (merge.pulse)
      starts.set(
        merge.key,
        history || !accepted(merge.key) ? -Infinity : (base.get(merge.key) ?? now - merge.age),
      );
  return sameMap(starts, base) ? base : starts;
}
export function mergingChanges(starts: Starts, now: number, reduced: boolean) {
  const delay = arrivalDelay();
  return [...starts].flatMap(([key, start]) =>
    !reduced && now < start + delay ? [changeId(key)] : [],
  );
}
function presentation(
  state: State,
  starts: Starts,
  now: number,
  reduced: boolean,
  immediate: ReadonlySet<string>,
) {
  const merging = mergingChanges(starts, now, reduced).filter((id) => !immediate.has(id));
  const labels = new Map<string, "Merging" | "Merged">();
  for (const change of Object.values(state.changes))
    if (change.resolution?.merge === "merged")
      labels.set(change.change_id, merging.includes(change.change_id) ? "Merging" : "Merged");
  return { starts, merging, labels };
}
export class ArrivalClock {
  private milestones!: MilestoneLife;
  private immediate = new Set<string>();
  private listeners = new Set<() => void>();
  private timer?: ReturnType<typeof setTimeout>;
  private snapshot!: ReturnType<typeof presentation>;
  private state!: State;
  constructor(state: State) {
    Object.assign(this, this.derive(state, 0, true));
  }
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  derive(state: State, now = performance.now(), history = false) {
    const reduced =
      typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    // Copy the life data (sets, maps and event records), retaining its prototype. Speculative
    // renders can run the same acceptance/cap calculation without consuming committed milestones.
    const milestones = Object.assign(
      new MilestoneLife(getActiveTheme()),
      history ? {} : structuredClone(this.milestones),
    );
    milestones.setLook(getActiveTheme());
    milestones.update(selectMilestones(state), now, reduced, history);
    const starts = mergeArrivals(
      state,
      history ? new Map() : this.snapshot.starts,
      now,
      history,
      (key) => milestones.accepted(key),
    );
    const immediate = new Set(
      [...starts.keys()].map(changeId).filter((id) => reduced || this.immediate.has(id)),
    );
    const next = presentation(state, starts, now, reduced, immediate);
    return {
      state,
      milestones,
      immediate,
      snapshot:
        !history && starts === this.snapshot.starts && sameMap(next.labels, this.snapshot.labels)
          ? this.snapshot
          : next,
    };
  }
  update(state: State) {
    const now = performance.now();
    const previous = this.snapshot;
    Object.assign(this, this.derive(state, now));
    if (this.snapshot !== previous) {
      for (const listener of this.listeners) listener();
    }
    clearTimeout(this.timer);
    const delay = arrivalDelay();
    const pending = [...this.snapshot.starts.values()]
      .map((start) => start + delay - now)
      .filter((ms) => ms > 0);
    if (pending.length)
      this.timer = setTimeout(
        () => {
          this.update(this.state);
        },
        Math.min(...pending),
      );
  }
  dispose() {
    clearTimeout(this.timer);
  }
}
export function useMergeArrival(state: State) {
  const [clock] = useState(() => new ArrivalClock(state));
  useSyncExternalStore(clock.subscribe, clock.getSnapshot, clock.getSnapshot);
  useLayoutEffect(() => {
    clock.update(state);
    const query = matchMedia("(prefers-reduced-motion: reduce)");
    query.onchange = () => {
      clock.update(state);
    };
    return () => {
      query.onchange = null;
      clock.dispose();
    };
  }, [clock, state]);
  return clock.derive(state).snapshot;
}
export type MergeArrival = ReturnType<typeof useMergeArrival>;
