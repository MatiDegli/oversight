// One presentation clock shared by the field, card and pending marks; never reducer state.
import { useEffect, useLayoutEffect, useState, useSyncExternalStore } from "react";
// eslint-disable-next-line no-restricted-imports -- C1 uses a pure leaf to isolate graph startup.
import { afterlightOf } from "../graph/field/afterlight";
// eslint-disable-next-line no-restricted-imports -- C1 uses a pure leaf to isolate graph startup.
import { mergeArrivalDelay, MilestoneLife } from "../graph/field/milestones";
// eslint-disable-next-line no-restricted-imports -- C1 uses a pure leaf to isolate graph startup.
import { selectMerges, selectMilestones } from "../graph/field/life";
import type { State } from "../state";
import { getActiveTheme } from "../theme";
type Starts = ReadonlyMap<string, number>;
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
  const delay = mergeArrivalDelay(afterlightOf(getActiveTheme()));
  return [...starts]
    .filter(([, start]) => !reduced && now < start + delay)
    .map(([key]) => key.slice(0, key.lastIndexOf(":")));
}
function presentation(
  state: State,
  starts: Starts,
  now: number,
  reduced: boolean,
  immediate: ReadonlySet<string> = new Set(),
) {
  const merging = mergingChanges(starts, now, reduced).filter((id) => !immediate.has(id));
  const labels = new Map<string, "Merging" | "Merged">();
  for (const change of Object.values(state.changes))
    if (change.resolution?.merge === "merged")
      labels.set(change.change_id, merging.includes(change.change_id) ? "Merging" : "Merged");
  return { starts, merging, labels };
}
export class ArrivalClock {
  private starts: Starts;
  private milestones = new MilestoneLife(getActiveTheme());
  private immediate = new Set<string>();
  private listeners = new Set<() => void>();
  private timer?: ReturnType<typeof setTimeout>;
  private snapshot: ReturnType<typeof presentation>;
  private reduced =
    typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  constructor(private state: State) {
    this.milestones.update(selectMilestones(state), 0, this.reduced, true);
    this.starts = mergeArrivals(state, new Map(), 0, true);
    this.snapshot = presentation(state, this.starts, 0, this.reduced);
  }
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  update(state: State, reduced = this.reduced) {
    const now = performance.now();
    this.milestones.setLook(getActiveTheme());
    this.milestones.update(selectMilestones(state), now, reduced, false);
    this.starts = mergeArrivals(state, this.starts, now, false, (key) =>
      this.milestones.accepted(key),
    );
    const ids = new Set([...this.starts.keys()].map((key) => key.slice(0, key.lastIndexOf(":"))));
    this.immediate = new Set([...this.immediate].filter((id) => ids.has(id)));
    if (reduced) for (const id of ids) this.immediate.add(id);
    this.state = state;
    this.reduced = reduced;
    const next = presentation(state, this.starts, now, reduced, this.immediate);
    if (next.starts !== this.snapshot.starts || !sameMap(next.labels, this.snapshot.labels)) {
      this.snapshot = next;
      for (const listener of this.listeners) listener();
    }
    clearTimeout(this.timer);
    const delay = mergeArrivalDelay(afterlightOf(getActiveTheme()));
    const pending = [...this.starts.values()]
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
  motion(reduced: boolean) {
    this.update(this.state, reduced);
  }
  dispose() {
    clearTimeout(this.timer);
  }
}
export function useMergeArrival(state: State) {
  const [clock] = useState(() => new ArrivalClock(state));
  const arrival = useSyncExternalStore(clock.subscribe, clock.getSnapshot, clock.getSnapshot);
  useLayoutEffect(() => {
    clock.update(state);
  }, [clock, state]);
  useEffect(() => {
    const query = matchMedia("(prefers-reduced-motion: reduce)");
    const changed = () => {
      clock.motion(query.matches);
    };
    query.addEventListener("change", changed);
    return () => {
      query.removeEventListener("change", changed);
      clock.dispose();
    };
  }, [clock]);
  return arrival;
}
export type MergeArrival = ReturnType<typeof useMergeArrival>;
