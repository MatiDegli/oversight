// Capture-only seam: the scan uses the selected log with settled timing, even when the public
// replay has a bundled recording. Preserve its label; this seam never enters the product build.
import settled from "../../fixtures/events/handwritten-level1.jsonl?raw";
import { replaySource as bundled, startReplay } from "../src/transport/index";
import type { TransportStore } from "../src/transport/index";

export * from "../src/transport/index";
export const replaySource = { ...bundled, text: settled };

export function startAppTransport(store: TransportStore) {
  return startReplay({ store, source: replaySource, speed: 0 });
}
