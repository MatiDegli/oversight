import fixture from "../../../fixtures/events/handwritten-level1.jsonl?raw";

import type { TransportStore } from "./events";
import { startLive } from "./live";
import { startReplay } from "./replay";
import { chooseReplaySource } from "./replay-source";
import type { RecordedReplay } from "./replay-source";

export { CommandError, createCommandClient } from "./commands";
export { commandFailure } from "./failure";
export type { Transport, TransportStore } from "./events";
export { startLive } from "./live";
export type { EventStream, LiveOptions } from "./live";
export { startReplay } from "./replay";
export type { ReplayOptions, ReplaySource } from "./replay";
export { chooseReplaySource } from "./replay-source";
export type { ChosenReplaySource, ReplayKind } from "./replay-source";

// A missing recorded log is not a build error: the glob simply matches nothing, and the
// replay build falls back to the hand-written fixture below.
const recordedLogs = import.meta.glob<RecordedReplay>(
  "../../../fixtures/events/recorded-demo.jsonl",
  {
    query: "?recorded-replay",
    import: "default",
    eager: true,
  },
);
const recorded = Object.values(recordedLogs)[0];

// `vite build --mode replay` produces a static demo with no backend requests. It replays a
// real recorded run when `make record` has bundled one, labelled as one; otherwise it
// replays the hand-written fixture, labelled as one.
export const replayBuild = import.meta.env.MODE === "replay";
export const replaySource = chooseReplaySource(recorded?.text, fixture, recorded?.provenance);
export function startAppTransport(store: TransportStore, onError?: (error: unknown) => void) {
  return replayBuild ? startReplay({ store, source: replaySource }) : startLive({ store, onError });
}
