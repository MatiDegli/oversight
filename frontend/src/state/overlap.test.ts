import type { AgentFocus, ChangeResolved, ServerEvent } from "@contracts";
import { describe, expect, it } from "vitest";
import scripted from "../../../fixtures/events/scripted-level2.jsonl?raw";
import { applyEvent, createInitialState } from "./index";
import type { State } from "./types";
import { selectOverlap } from "./overlap";
import {
  baseMoment,
  priceId,
  readMoment,
  recordedEvents,
  sameNodeMoment,
  required,
} from "./overlap.fixture";

const empty = { sharedEdits: [], readOfOthers: [] };
const entry = (agentId: string, slot: "A" | "B") => ({
  agentId,
  slot,
  nodes: [{ id: priceId, name: "compute_price" }],
});
const next = (state: State, event: ServerEvent) =>
  applyEvent(state, { ...event, seq: state.lastSeq + 1 });
function focus(state: State, ids: string[], mode: AgentFocus["mode"] = "read", agent = "agent-2") {
  return next(state, {
    seq: 0,
    ts: state.clock ?? "",
    type: "agent.focus",
    payload: { agent_id: agent, mode, node_ids: ids, ranges: [] },
  });
}
function resolve(
  state: State,
  decision: ChangeResolved["decision"],
  conflict = false,
  stopped = false,
) {
  return next(state, {
    seq: 0,
    ts: state.clock ?? "",
    type: "change.resolved",
    payload: {
      ...required(recordedEvents.find((event) => event.type === "change.resolved")).payload,
      decision,
      merge: decision === "approved" ? (conflict ? "conflict" : "merged") : null,
      reason: stopped ? "stopped" : null,
      merge_commit: null,
    },
  });
}

describe("change-lifetime observed reads", () => {
  it("accumulates every read once in first-read order, retaining it after edit and empty focus", () => {
    let state: State = { ...baseMoment(), changeReads: {} };
    state = focus(state, ["z", "a", "z"]);
    state = focus(state, ["a", "b"]);
    state = focus(state, ["edit-only"], "edit");
    state = focus(state, []);
    expect(state.changeReads["change-2"]).toEqual(["z", "a", "b"]);
    expect(state.lastReads["agent-2"]).toBeUndefined();
    expect(state.focus["agent-2"]?.node_ids).toEqual([]);
    expect(focus(state, ["unknown"], "read", "no-status").changeReads).toEqual(state.changeReads);
  });
  it.each([
    ["changes_requested", false, false, true],
    ["approved", false, false, false],
    ["rejected", false, false, false],
    ["rejected", false, true, false],
    ["approved", true, false, true],
  ] as const)(
    "resolution %s conflict=%s stopped=%s keeps=%s",
    (decision, conflict, stopped, keeps) => {
      const state = focus(baseMoment(), [priceId], "read", "agent-1");
      const after = resolve(state, decision, conflict, stopped);
      expect(after.changeReads["change-1"]).toEqual(
        keeps ? state.changeReads["change-1"] : undefined,
      );
      expect(after.changeReads["change-2"]).toEqual(state.changeReads["change-2"]);
      expect(state.changeReads["change-1"]).toContain(priceId);
    },
  );
  it("starts empty for a new change id and resets reads on run.started", () => {
    let state = readMoment();
    const agent = required(state.agents["agent-2"]);
    state = next(state, {
      seq: 0,
      ts: state.clock ?? "",
      type: "agent.status",
      payload: { ...agent, change_id: "new-change" },
    });
    expect(state.changeReads["new-change"] ?? []).toEqual([]);
    expect(selectOverlap(state, agent.agent_id)).toEqual(empty);
    state = focus(state, ["new-read"]);
    expect(state.changeReads["new-change"]).toEqual(["new-read"]);
    const start = required(recordedEvents[0]);
    if (start.type !== "run.started") throw new Error("Missing start");
    state = applyEvent(state, { ...start, payload: { ...start.payload, run_id: "new-run" } });
    expect(state.changeReads).toEqual({});
  });
});

it("reports the read on B only, then shared edits on both without repeating the read", () => {
  expect(selectOverlap(readMoment(), "agent-2")).toEqual({
    sharedEdits: [],
    readOfOthers: [entry("agent-1", "A")],
  });
  expect(selectOverlap(readMoment(), "agent-1")).toEqual(empty);
  expect(selectOverlap(sameNodeMoment(), "agent-1")).toEqual({
    sharedEdits: [entry("agent-2", "B")],
    readOfOthers: [],
  });
  expect(selectOverlap(sameNodeMoment(), "agent-2")).toEqual({
    sharedEdits: [entry("agent-1", "A")],
    readOfOthers: [],
  });
  const approved = resolve(sameNodeMoment(), "approved");
  for (const id of ["agent-1", "agent-2"]) expect(selectOverlap(approved, id)).toEqual(empty);
});

it.each([
  { events: recordedEvents },
  {
    events: scripted
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as ServerEvent),
  },
])("keeps the existing logs free of overlap at every seq", ({ events }) => {
  let state = createInitialState();
  for (const event of events) {
    state = applyEvent(state, event);
    for (const id of Object.keys(state.agents))
      expect(selectOverlap(state, id), `seq ${String(event.seq)}`).toEqual(empty);
  }
});
it("preserves lastReads independently of lifetime reads and edit focus", () => {
  expect(baseMoment().lastReads).toEqual({
    "agent-1": "tests.test_pricing",
    "agent-2": "tests.test_inventory",
  });
  const expected = { "agent-1": "tests.test_pricing", "agent-2": "shop.pricing" };
  expect(readMoment().lastReads).toEqual(expected);
  expect(sameNodeMoment().lastReads).toEqual(expected);
  expect(sameNodeMoment().focus["agent-2"]?.node_ids).toEqual([priceId]);
});
