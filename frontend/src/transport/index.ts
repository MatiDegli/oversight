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
export const replaySource = { text: fixture, label: "Replay · hand-written demo fixture" };
export function startAppTransport(store: TransportStore, onError?: (error: unknown) => void) {
  return replayBuild ? startReplay({ store, source: replaySource }) : startLive({ store, onError });
}
