// Shared test setup; never imported by the application.
import type { ServerEvent } from "@contracts";

import log from "../../fixtures/events/handwritten-level1.jsonl?raw";
import brakedLog from "../../fixtures/events/handwritten-two-slots-braked.jsonl?raw";
import { applyEvent, createInitialState } from "./state";

export function twoSlotState(braked = false) {
  const events = brakedLog
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as ServerEvent);
  const state = events
    .filter((event) => braked || event.seq <= 2)
    .reduce(applyEvent, createInitialState());
  if (!braked && state.run) {
    const opening = JSON.parse(log.trim().split("\n")[0] ?? "null") as ServerEvent;
    if (opening.type !== "run.started") throw new Error("Missing opening run fixture");
    // Offering both models must preserve the normal run's unbraked opening budget.
    // The full braked log keeps its continuous ledger total for replay and shots.
    state.run = { ...state.run, budget: opening.payload.budget };
  }
  return state;
}
