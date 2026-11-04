import type { LeadStatus, ModelProfile, ServerEvent } from "@contracts";
import { expect, it } from "vitest";
import recorded from "../../../fixtures/events/recorded-demo.jsonl?raw";
import { applyEvent, createInitialState } from "./index";
import { createStateStore } from "./store";
import { agent, event, run } from "./state.support";
// eslint-disable-next-line no-restricted-imports -- Exercise the production replay player without extending the transport facade for a test.
import { createReplayPlayer } from "../transport/replayPlayer";
import { isScriptedAgent } from "./scriptedAgents";

const models: ModelProfile[] = [
  ...run.models,
  { id: "arbitrary-id", label: "Muse Spark", provider: "fake", model: "anything", reasoning: null },
  { id: "muse", label: "Scripted demo agents", provider: "meta", model: "fake", reasoning: null },
  { id: "open", label: "fake", provider: "baseten", model: "fake", reasoning: null },
];
const lead: LeadStatus = {
  agent_id: "lead-1",
  revision: 1,
  task: "fake Scripted demo agents",
  model: "fake",
  state: "working",
  step: 0,
  detail: null,
  limits: null,
  requested_plan_id: null,
};
const start = event(1, "run.started", { ...run, models });
const fold = (events: ServerEvent[]) => events.reduce(applyEvent, createInitialState());
const coder = (seq: number, model: string, id = "agent-1") =>
  event(seq, "agent.status", { ...agent, agent_id: id, model, task: "fake Scripted demo agents" });

it.each(["fake", "arbitrary-id"])("uses only the fake provider for profile %s", (model) => {
  const state = fold([start, coder(2, model)]);
  expect(isScriptedAgent(state, "agent-1")).toBe(true);
});
it("labels only scripted members in a mixed Meta/Baseten/scripted run", () => {
  const state = fold([
    start,
    coder(2, "fake"),
    coder(3, "muse", "agent-2"),
    coder(4, "open", "agent-3"),
  ]);
  expect(["agent-1", "agent-2", "agent-3"].map((id) => isScriptedAgent(state, id))).toEqual([
    true,
    false,
    false,
  ]);
});
it("ignores text, labels, and merely offering a scripted profile", () => {
  const state = fold([start, coder(2, "muse")]);
  expect(isScriptedAgent(state, "agent-1")).toBe(false);
  expect(
    isScriptedAgent(
      fold([start, event(2, "lead.status", { ...lead, model: "muse" })]),
      lead.agent_id,
    ),
  ).toBe(false);
});
it("withholds provenance without a run, status or matching profile", () => {
  expect(isScriptedAgent(createInitialState(), "agent-1")).toBe(false);
  expect(isScriptedAgent(fold([start]), "agent-1")).toBe(false);
  expect(isScriptedAgent(fold([start, coder(2, "unknown")]), "agent-1")).toBe(false);
  expect(
    isScriptedAgent(
      fold([start, event(2, "lead.status", { ...lead, model: "unknown" })]),
      lead.agent_id,
    ),
  ).toBe(false);
});
it("uses the latest coder status and preserves provenance after termination", () => {
  const state = fold([
    start,
    coder(2, "muse"),
    coder(3, "fake"),
    event(4, "agent.status", { ...agent, state: "done" }),
  ]);
  expect(isScriptedAgent(state, "agent-1")).toBe(true);
  expect(isScriptedAgent(applyEvent(state, coder(5, "muse")), "agent-1")).toBe(false);
});
it.each(["fake", "muse"])("uses the newest lead revision, ignoring a stale %s status", (stale) => {
  const current = stale === "fake" ? "muse" : "arbitrary-id";
  const state = fold([
    start,
    event(2, "lead.status", lead),
    event(3, "lead.status", { ...lead, revision: 3, model: current, state: "stopped" }),
    event(4, "lead.status", { ...lead, revision: 2, model: stale }),
  ]);
  expect(isScriptedAgent(state, lead.agent_id)).toBe(current === "arbitrary-id");
});
it("resets both coder and lead provenance on a new run", () => {
  const state = fold([start, coder(2, "fake"), event(3, "lead.status", lead)]);
  const reset = applyEvent(state, event(1, "run.started", { ...run, run_id: "run-2", models }));
  expect(["agent-1", lead.agent_id].map((id) => isScriptedAgent(reset, id))).toEqual([
    false,
    false,
  ]);
});
it("agrees through store applyEvents and replay, including stale revisions", async () => {
  const events = [
    start,
    coder(2, "fake"),
    coder(3, "muse", "agent-2"),
    event(4, "lead.status", lead),
    event(5, "lead.status", { ...lead, revision: 2, model: "muse" }),
    event(6, "lead.status", lead),
  ];
  const live = createStateStore();
  live.getState().applyEvents(events);
  const replay = createStateStore();
  const player = createReplayPlayer({
    store: replay,
    source: {
      text: events.map((e) => JSON.stringify(e)).join("\n"),
    },
  });
  try {
    await player.ready;
    player.seekSeq(6);
    expect(replay.getState().state.gap).toBe(false);
    const ids = ["agent-1", "agent-2", lead.agent_id];
    const result = ids.map((id) => isScriptedAgent(live.getState().state, id));
    expect(result).toEqual([true, false, false]);
    expect(ids.map((id) => isScriptedAgent(replay.getState().state, id))).toEqual(result);
  } finally {
    player.dispose();
  }
});
it("labels no agent at any point of the bundled real recording", () => {
  let state = createInitialState();
  for (const line of recorded.trim().split("\n")) {
    state = applyEvent(state, JSON.parse(line) as ServerEvent);
    expect(state.gap).toBe(false);
    expect(Object.keys(state.agents).some((id) => isScriptedAgent(state, id))).toBe(false);
  }
});
