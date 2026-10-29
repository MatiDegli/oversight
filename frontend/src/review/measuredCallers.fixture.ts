// Hand-written fixture shared by unit and browser tests; never imported by the application.
import type { ChangeFacts, MeasuredCaller, ServerEvent } from "@contracts";
import log from "../../../fixtures/events/handwritten-measured-cone.jsonl?raw";
import { applyEvent, createInitialState } from "../state";

export const measuredEvents = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
export const measuredState = measuredEvents
  .filter((event) => event.seq <= 20)
  .reduce(applyEvent, createInitialState());
export const measuredFacts = measuredEvents.flatMap((event) =>
  event.type === "change.facts_recorded" ? [event.payload] : [],
);
export const availableFacts = measuredFacts[0] as ChangeFacts;
export const firstCaller = availableFacts.measured_cone?.[0] as MeasuredCaller;

export function withMeasuredFacts(facts = availableFacts) {
  return applyEvent(measuredState, {
    seq: 21,
    ts: "2026-10-29T15:00:00Z",
    type: "change.facts_recorded",
    payload: facts,
  });
}
