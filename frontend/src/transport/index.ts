import fixture from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import recorded from "../../../fixtures/events/recorded-demo.jsonl?recorded-replay";

import type { TransportStore } from "./events";
import { startLive } from "./live";
import { startReplay } from "./replay";
import { chooseReplaySource } from "./replay-source";

export { CommandError, createCommandClient } from "./commands";
export { commandFailure } from "./failure";
export type { Transport, TransportStore } from "./events";
export { startLive } from "./live";
export type { EventStream, LiveOptions } from "./live";
export { startReplay } from "./replay";
export type { ReplayOptions, ReplaySource } from "./replay";
export { parseEvent } from "./events";
export type { RecordedReplay, RecordingProvenance } from "./replay-source";
export const replayRecording = recorded;
export { chooseReplaySource } from "./replay-source";
export type { ChosenReplaySource, ReplayKind } from "./replay-source";

// `vite build --mode replay` produces a static demo with no backend requests. It replays a
// verified real recorded run. Require the bundle explicitly: a missing recording must fail
// the build rather than silently publish the hand-written fixture. Test seams choose their
// own fixtures with chooseReplaySource(null, fixture).
export const replayBuild = import.meta.env.MODE === "replay";
export const replaySource = chooseReplaySource(recorded.text, fixture, recorded.provenance);
export function startAppTransport(
  store: TransportStore,
  onError?: (error: unknown) => void,
  onOpen?: () => void,
) {
  return replayBuild
    ? startReplay({ store, source: replaySource })
    : startLive({ store, onError, onOpen });
}
