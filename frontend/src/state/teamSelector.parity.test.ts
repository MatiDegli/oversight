import { expect, it } from "vitest";
import recorded from "../../../fixtures/events/recorded-demo.jsonl?raw";
import leadPlans from "../../../fixtures/events/handwritten-lead-plans.jsonl?raw";
import golden from "../../../fixtures/events/team-selector.golden.json";
import { parseEvent } from "../transport";
import { applyEvent } from "./applyEvent";
import { selectTeams } from "./teamSelector";
import { createInitialState } from "./types";

it.each([
  { name: "recorded-demo.jsonl" as const, text: recorded },
  { name: "handwritten-lead-plans.jsonl" as const, text: leadPlans },
])("matches the server team identity and first-start order for $name", ({ name, text }) => {
  const state = text.trim().split("\n").map(parseEvent).reduce(applyEvent, createInitialState());
  expect(state.gap).toBe(false);
  expect(selectTeams(state)).toEqual(golden[name]);
  if (name === "handwritten-lead-plans.jsonl") {
    expect(state.agents).toEqual({});
    expect(selectTeams(state).byAgent["agent-1"]).toEqual({ team: "agent-1", kind: "lead" });
  }
});
