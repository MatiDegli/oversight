// Build-only seam: /level2 uses App's real command/file wiring against page.route, while its
// state still comes exclusively from startReplay. The public replay keeps commands disabled.
import level2 from "../../fixtures/events/scripted-level2.jsonl?raw";
import {
  replaySource,
  startAppTransport as startLevel1,
  startReplay,
} from "../src/transport/index";
import type { TransportStore } from "../src/transport/index";

export * from "../src/transport/index";
export { replaySource };
export const replayBuild = window.location.pathname !== "/level2";

export function startAppTransport(store: TransportStore, onError?: (error: unknown) => void) {
  if (replayBuild) return startLevel1(store, onError);
  return startReplay({ store, source: { text: level2 } });
}
