import { readFileSync } from "node:fs";

import type { ServerEvent } from "@contracts";

import { applyEvent } from "./applyEvent";
import { createInitialState } from "./types";
import type { State } from "./types";

// The shared log is schema-validated by the contracts tests. Variations below keep its payloads.
export const panelEvents = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);

export function through(seq: number): State {
  return panelEvents.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());
}

export function appendEvent(state: State, event: ServerEvent): State {
  return applyEvent(state, { ...event, seq: state.lastSeq + 1 });
}
