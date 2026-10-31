// Hand-written evidence scenarios, never recorded model or probe execution.
import { readFileSync } from "node:fs";

import type { ChangeFacts, ServerEvent } from "@contracts";

import { applyEvent, createInitialState } from "../state";

function events(path: string): ServerEvent[] {
  return readFileSync(new URL(path, import.meta.url), "utf8")
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as ServerEvent);
}
export const behaviorState = events("../../../fixtures/events/handwritten-level1.jsonl")
  .filter((event) => event.seq <= 20)
  .reduce(applyEvent, createInitialState());
export const behaviorEvents = events(
  "../../../fixtures/events/handwritten-behavior-review.jsonl",
).filter((event) => event.type === "change.facts_recorded");
export const behaviorFacts = behaviorEvents.map((event) => {
  return event.payload;
});
export function withBehaviorFacts(...facts: ChangeFacts[]) {
  return { ...behaviorState, changeFacts: { "change-1": facts } };
}

export function required<T>(value: T | null | undefined): T {
  if (value == null) throw new Error("Missing hand-written test evidence");
  return value;
}
