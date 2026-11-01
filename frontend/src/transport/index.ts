import fixture from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import recorded from "../../../fixtures/events/recorded-demo.jsonl?recorded-replay";

import type { TransportStore } from "./events";
import { startLive } from "./live";
import type { ReplayPlayer } from "./replayPlayer";
import { chooseReplaySource } from "./replay-source";

export { CommandError, createCommandClient } from "./commands";
export { commandFailure } from "./failure";
export type { ReplayPlayer, ReplaySnapshot, ReplayRate } from "./replayPlayer";
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
if (replayBuild && recorded.text.trim() === "") {
  throw new Error("Replay build requires a non-empty recording");
}
export const replaySource = chooseReplaySource(
  recorded.text,
  replayBuild ? "" : fixture,
  recorded.provenance,
);
export function startAppTransport(
  store: TransportStore,
  onError?: (error: unknown) => void,
  onOpen?: () => void,
  onReplay?: (player: ReplayPlayer) => void,
) {
  if (!replayBuild) return startLive({ store, onError, onOpen });
  let stopped = false;
  const isStopped = () => stopped;
  let player: ReplayPlayer | undefined;
  const ready = import("./replayPlayer").then(async ({ createReplayPlayer }) => {
    if (isStopped()) return;
    player = createReplayPlayer({ store, source: replaySource });
    await player.ready;
    if (!isStopped()) onReplay?.(player);
  });
  return {
    ready,
    stop: () => {
      stopped = true;
      player?.dispose();
    },
  };
}
