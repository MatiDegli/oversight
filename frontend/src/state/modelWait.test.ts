import { readFileSync } from "node:fs";
import type { ModelAttemptFailed, ServerEvent } from "@contracts";
import { expect, it } from "vitest";
import { applyEvent } from "./applyEvent";
import { createInitialState } from "./types";

const events = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const initial = events.slice(0, 18).reduce(applyEvent, createInitialState());
const failure: ModelAttemptFailed = {
  agent_id: "agent-1",
  change_id: "change-1",
  role: "agent",
  model: "muse-spark",
  provider: "meta",
  attempt: 1,
  max_attempts: 5,
  cause: "rate_limited",
  status: 429,
  retry_after_s: 2,
  wait_s: 2,
  final: null,
};
const failed = (payload = failure): ServerEvent => ({
  seq: 19,
  ts: initial.clock ?? "",
  type: "model.attempt_failed",
  payload,
});
const waiting = () => applyEvent(initial, failed());
function recorded(type: ServerEvent["type"]) {
  const event = events.find((event) => event.type === type);
  if (!event) throw new Error("Missing fixture");
  return event;
}

it.each(["agent", null] as const)("retains waiting call data for role %s immutably", (role) => {
  const state = applyEvent(initial, failed({ ...failure, role, wait_s: 0 }));
  expect(state.modelWaits["agent-1"]).toEqual({
    seq: 19,
    attempt: 1,
    max_attempts: 5,
    cause: "rate_limited",
    wait_s: 0,
  });
  expect(initial.modelWaits).toEqual({});
});

it.each(["agent.step", "usage.tick", "model.attempt_failed", "run.started"] as const)(
  "clears on %s",
  (type) => {
    const source =
      type === "model.attempt_failed"
        ? failed({ ...failure, attempt: 5, wait_s: null, final: "attempts_used" })
        : recorded(type);
    const before = waiting();
    expect(applyEvent(before, { ...source, seq: 20 }).modelWaits).toEqual({});
    expect(before.modelWaits["agent-1"]).toBeDefined();
  },
);

it.each(["review", "plan", "lead"] as const)("does not wait for role %s", (role) => {
  const event = failed({ ...failure, role });
  expect(applyEvent(initial, event).modelWaits).toEqual({});
  expect(applyEvent(waiting(), { ...event, seq: 20 }).modelWaits).toEqual({});
});

it.each(["agent.step", "usage.tick", "model.attempt_failed"] as const)(
  "keeps the wait through another agent's %s",
  (type) => {
    const source = type === "model.attempt_failed" ? failed() : recorded(type);
    if (!("agent_id" in source.payload)) throw new Error("Missing agent event");
    const event = {
      ...source,
      seq: 20,
      payload: { ...source.payload, agent_id: "agent-2" },
    } as ServerEvent;
    const before = waiting();
    expect(applyEvent(before, event).modelWaits["agent-1"]).toEqual(before.modelWaits["agent-1"]);
  },
);
