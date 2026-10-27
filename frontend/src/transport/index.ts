import fixture from "../../../fixtures/events/handwritten-level1.jsonl?raw";

import type { TransportStore } from "./events";
import { startLive } from "./live";
import { startReplay } from "./replay";

export { CommandError, createCommandClient } from "./commands";
export type { Transport, TransportStore } from "./events";
export { startLive } from "./live";
export type { EventStream, LiveOptions } from "./live";
export { startReplay } from "./replay";
export type { ReplayOptions, ReplaySource } from "./replay";

// `vite build --mode replay` produces a static demo with no backend requests.
// Replace the fixture with a real recorded run before publishing the judges' demo.
export const replayBuild = import.meta.env.MODE === "replay";
// `kind` says what the log is, in words a sentence can hold; the top-left label and the review panel
// both take it, so a hand-written log never calls itself a recording. A real run reads "real recorded run".
const replayKind = "hand-written demo fixture";
export const replaySource = { text: fixture, kind: replayKind, label: `Replay · ${replayKind}` };
export function startAppTransport(store: TransportStore, onError?: (error: unknown) => void) {
  return replayBuild ? startReplay({ store, source: replaySource }) : startLive({ store, onError });
}
