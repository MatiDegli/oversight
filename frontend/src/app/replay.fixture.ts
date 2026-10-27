import { readFileSync } from "node:fs";

import type { ServerEvent } from "@contracts";

import { applyEvent, createInitialState } from "../state";
import type { State } from "../state";

// The level 1 log, schema-checked by the contracts tests: one agent edits compute_price, proposes
// at seq 19, is ready at 20, and is approved and merged at 21 and 22.
const events = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);

export function through(seq: number): State {
  return events.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());
}
