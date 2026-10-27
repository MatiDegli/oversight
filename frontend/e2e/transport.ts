// Build-only seam: /level2 uses App's real command/file wiring against page.route, while its
// state still comes exclusively from startReplay. The public replay keeps commands disabled.
import level2 from "../../fixtures/events/scripted-level2.jsonl?raw";
import level1 from "../../fixtures/events/handwritten-level1.jsonl?raw";
import { chooseReplaySource, startReplay } from "../src/transport/index";
import type { TransportStore } from "../src/transport/index";

export * from "../src/transport/index";
// Keep the e2e fixture independent of the public site's bundled recording.
export const replaySource = chooseReplaySource(null, level1);
export const replayBuild = window.location.pathname !== "/level2";

export function startAppTransport(store: TransportStore) {
  if (replayBuild) return startReplay({ store, source: replaySource });
  return startReplay({ store, source: { text: level2 } });
}
