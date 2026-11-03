import { expect, it } from "vitest";
import type { ServerEvent } from "@contracts";
import fixture from "../../../fixtures/views/reference-cases.json";
import { applyEvent } from "./applyEvent";
import { createInitialState } from "./types";
import { resolveViewReferences } from "./viewReferences";

const events = fixture.events as ServerEvent[];
it("keeps only accepted metadata, without payload or speech, including the start", () => {
  const state = events.reduce(applyEvent, createInitialState());
  expect(Object.keys(state.eventFacts)).toHaveLength(events.length);
  for (const event of events) {
    expect(state.eventFacts[event.seq]).toEqual({
      type: event.type,
      ts: event.ts,
      agentId: "agent_id" in event.payload ? event.payload.agent_id : null,
    });
  }
  const first = required(events[0]);
  const reset = applyEvent(state, {
    ...first,
    seq: 1,
    payload: { ...first.payload, run_id: "new-run" },
  } as ServerEvent);
  expect(reset.eventFacts).toEqual({ 1: state.eventFacts[1] });
  expect(state.lastSeq).toBe(15);
});
it("records neither gaps, rejected graph applications nor events without a run", () => {
  let state = events.slice(0, 2).reduce(applyEvent, createInitialState());
  const gap = applyEvent(state, { ...required(events[2]), seq: 9 });
  expect(gap.gap).toBe(true);
  expect(gap.eventFacts).toBe(state.eventFacts);
  expect(applyEvent(createInitialState(), required(events[1])).eventFacts).toEqual({});
  state = applyEvent(state, {
    seq: 3,
    ts: required(events[0]).ts,
    type: "graph.delta",
    payload: {
      from_version: "wrong",
      to_version: "g2",
      cause: "rescan",
      commit: "head",
      nodes_added: [],
      nodes_changed: [],
      nodes_removed: [],
      edges_added: [],
      edges_changed: [],
      edges_removed: [],
      parse_errors: [],
    },
  });
  expect(state.gap).toBe(true);
  expect(state.eventFacts[3]).toBeUndefined();
});
it("leaves event facts unchanged for an accepted dormant preview status", () => {
  const state = events.reduce(applyEvent, createInitialState());
  const next = applyEvent(state, {
    seq: state.lastSeq + 1,
    ts: "2026-11-03T00:00:01.000Z",
    type: "preview.status",
    payload: {
      preview_id: "preview-" + "a".repeat(32),
      run_id: "run-20261027-140312",
      agent_id: "agent-1",
      change_id: "change-1",
      revision: 1,
      head_commit: "c1",
      state: "starting",
      reason: null,
    },
  });
  expect(next.eventFacts).toBe(state.eventFacts);
});
it("retains accepted moments after more than 32 later milestones, bounded by both horizons", () => {
  let state = events.slice(0, 6).reduce(applyEvent, createInitialState());
  for (let i = 0; i < 40; i++)
    state = applyEvent(state, {
      seq: state.lastSeq + 1,
      ts: required(events[0]).ts,
      type: "error",
      payload: {
        agent_id: null,
        change_id: null,
        message: "Synthetic milestone",
        source: "server",
        fatal: false,
      },
    });
  const request = {
    agent_id: "agent-9",
    through_seq: 6,
    caption: null,
    view: { kind: "moment" as const, event_seq: 6 },
  };
  expect(resolveViewReferences(state, request)).toEqual([6]);
  expect(resolveViewReferences(state, { ...request, through_seq: 5 })).toEqual([]);
  expect(resolveViewReferences({ ...state, lastSeq: 5 }, request)).toEqual([]);
});

function required<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("Missing synthetic fixture value");
  return value;
}
