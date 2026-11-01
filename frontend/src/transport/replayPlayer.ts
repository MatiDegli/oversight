import type { ServerEvent } from "@contracts";
import { applyEvent } from "../state";
import { parseEvent } from "./events";
import type { ReplayOptions } from "./replay";
import { replayOffsets, replayPrefix } from "./replayTimeline";

export type ReplayRate = 1 | 2 | 4;
export interface ReplaySnapshot {
  events: readonly ServerEvent[];
  offsets: readonly number[];
  position: number;
  duration: number;
  status: "playing" | "paused" | "ended";
  speed: ReplayRate;
  revision: number;
}
export interface ReplayPlayer {
  ready: Promise<void>;
  getSnapshot: () => ReplaySnapshot;
  subscribe: (listener: () => void) => () => void;
  play: () => void;
  pause: () => void;
  seek: (position: number) => void;
  restart: () => void;
  setSpeed: (speed: ReplayRate) => void;
  dispose: () => void;
}

/** One session owns the clock, pending work and publication capability. */
export function createReplayPlayer({
  store,
  source,
  request = fetch,
}: Omit<ReplayOptions, "speed">): ReplayPlayer {
  const abort = new AbortController();
  const listeners = new Set<() => void>();
  let snapshot: ReplaySnapshot = {
    events: [],
    offsets: [],
    position: 0,
    duration: 0,
    status: "paused",
    speed: 1,
    revision: 0,
  };
  let timer: ReturnType<typeof setTimeout>;
  let generation = 0;
  let anchor = performance.now();
  const publish = (next: Partial<ReplaySnapshot>) => {
    snapshot = { ...snapshot, ...next };
    listeners.forEach((listener) => {
      listener();
    });
  };
  const cancel = () => {
    generation++;
    clearTimeout(timer);
  };
  const positionNow = () =>
    Math.min(
      snapshot.duration,
      snapshot.position +
        (snapshot.status === "playing" ? (performance.now() - anchor) * snapshot.speed : 0),
    );
  const advance = (position: number) => {
    let state = store.getState().state;
    for (const [index, event] of snapshot.events.entries()) {
      if (event.seq > state.lastSeq && (snapshot.offsets[index] ?? Infinity) <= position) {
        state = applyEvent(state, event);
        if (state.gap)
          throw new Error(`Replay sequence or graph version mismatch at ${String(event.seq)}`);
      }
    }
    if (state !== store.getState().state) store.setState({ state });
    anchor = performance.now();
    publish({ position, status: position >= snapshot.duration ? "ended" : snapshot.status });
  };
  const schedule = () => {
    const token = generation;
    if (snapshot.status !== "playing" || abort.signal.aborted) return;
    timer = setTimeout(
      () => {
        if (token !== generation || abort.signal.aborted) return;
        advance(positionNow());
        schedule();
      },
      Math.min(40, (snapshot.duration - snapshot.position) / snapshot.speed),
    );
  };
  const seek = (position: number, status = snapshot.status) => {
    if (abort.signal.aborted || !snapshot.events.length) return;
    cancel();
    const clamped = Math.max(0, Math.min(snapshot.duration, Number.isNaN(position) ? 0 : position));
    const state = replayPrefix(snapshot.events, snapshot.offsets, clamped);
    store.setState({ state });
    anchor = performance.now();
    publish({
      position: clamped,
      status: clamped >= snapshot.duration ? "ended" : status === "ended" ? "paused" : status,
      revision: snapshot.revision + 1,
    });
    schedule();
  };
  const ready = (async () => {
    const response = "url" in source ? await request(source.url, { signal: abort.signal }) : null;
    if (response && !response.ok)
      throw new Error(`Replay log request failed: ${String(response.status)}`);
    const text = "text" in source ? source.text : response ? await response.text() : "";
    if (abort.signal.aborted) return;
    const events = text
      .split(/\r?\n/)
      .filter((line) => line.trim())
      .map(parseEvent);
    if (events[0]?.type !== "run.started") throw new Error("Replay must start with run.started");
    const offsets = replayOffsets(events);
    const duration = offsets.at(-1) ?? 0;
    replayPrefix(events, offsets, duration); // Validate the entire recording before publishing.
    snapshot = { ...snapshot, events, offsets, duration };
    seek(0, "playing");
  })().catch((error: unknown) => {
    if (!abort.signal.aborted) throw error;
  });
  return {
    ready,
    getSnapshot: () => snapshot,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    seek,
    restart: () => {
      seek(0, "playing");
    },
    play: () => {
      if (abort.signal.aborted || snapshot.status === "playing") return;
      if (snapshot.status === "ended") {
        seek(0, "playing");
        return;
      }
      anchor = performance.now();
      publish({ status: "playing" });
      schedule();
    },
    pause: () => {
      if (abort.signal.aborted || snapshot.status !== "playing") return;
      cancel();
      advance(positionNow());
      if (snapshot.position < snapshot.duration) publish({ status: "paused" });
    },
    setSpeed: (speed) => {
      if (abort.signal.aborted) return;
      if (![1, 2, 4].includes(speed)) throw new Error("Replay rate must be 1, 2 or 4");
      cancel();
      advance(positionNow());
      publish({ speed });
      schedule();
    },
    dispose: () => {
      abort.abort();
      cancel();
      listeners.clear();
    },
  };
}
