import type { ServerEvent } from "@contracts";

import { stateStore } from "../state";

/** Shared ingestion point for already validated events; this does not parse or fetch a log. */
export function loadGraphEvents(events: readonly ServerEvent[]) {
  stateStore.getState().applyEvents(events);
}

declare global {
  interface Window {
    oversightGraph?: { applyEvents: typeof loadGraphEvents };
  }
}

// Dev console: window.oversightGraph.applyEvents(events). Production has no console hook.
if (import.meta.env.DEV && typeof window !== "undefined") {
  window.oversightGraph = { applyEvents: loadGraphEvents };
}
