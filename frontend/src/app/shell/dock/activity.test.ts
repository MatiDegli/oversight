import { readFileSync } from "node:fs";
import type { ServerEvent } from "@contracts";
import { expect, it } from "vitest";
import { applyEvent, createInitialState, createStateStore } from "../../../state";
import { selectActivity } from "./selectors";

const fixture = readFileSync(
  new URL("../../../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const start = fixture.find((event) => event.type === "run.started");
const proposal = fixture.find((event) => event.type === "change.proposed");
const resolution = fixture.find((event) => event.type === "change.resolved");
const step = fixture.find((event) => event.type === "agent.step");
if (!start || !proposal || !resolution || !step) throw new Error("Missing replay events");
const source: ServerEvent[] = [
  start,
  { ...step, seq: 2 },
  { ...proposal, seq: 3 },
  {
    seq: 4,
    ts: start.ts,
    type: "error",
    payload: {
      source: "tools",
      message: "Read failed",
      agent_id: null,
      change_id: null,
      fatal: false,
    },
  },
  { ...resolution, seq: 5 },
  { ...step, seq: 6, payload: { ...step.payload, agent_id: "agent-2" } },
];
const log = source.map((event) => ({ ...event, ts: start.ts })); // Equal timestamps cannot establish order.

it("retains envelopes and globally orders all kinds identically in live batches and replay", () => {
  const recorded = JSON.parse(JSON.stringify(log)) as ServerEvent[];
  for (let cut = 1; cut <= log.length; cut++) {
    const live = createStateStore();
    live.getState().applyEvents(log.slice(0, cut - 1));
    live.getState().applyEvents(log.slice(cut - 1, cut));
    const replay = recorded.slice(0, cut).reduce(applyEvent, createInitialState());
    expect(live.getState().state).toEqual(replay);
    const entries = selectActivity(replay).entries;
    expect(entries.map((entry) => entry.seq)).toEqual(log.slice(1, cut).map((event) => event.seq));
    expect(entries.every((entry) => entry.time === start.ts)).toBe(true);
  }
  const state = log.reduce(applyEvent, createInitialState());
  expect(state.changes[proposal.payload.change_id]?.proposalEvent).toEqual({
    seq: 3,
    ts: start.ts,
  });
  expect(state.changes[resolution.payload.change_id]?.resolutionEvent).toEqual({
    seq: 5,
    ts: start.ts,
  });
  expect(state.errorEvents).toEqual([{ seq: 4, ts: start.ts }]);
  const gap = applyEvent(state, { ...proposal, seq: 8 });
  expect(gap.changes).toBe(state.changes);
  expect(gap.errorEvents).toBe(state.errorEvents);
  expect(applyEvent(state, { ...start, payload: { ...start.payload, run_id: "another" } })).toEqual(
    {
      ...createInitialState(),
      run: { ...start.payload, run_id: "another" },
      runStartedAt: start.ts,
      lastSeq: 1,
      clock: start.ts,
    },
  );
});

it("orders by seq even when timestamps run backwards", () => {
  const reversed = log.map((event) => ({
    ...event,
    ts: new Date((10 - event.seq) * 1000).toISOString(),
  }));
  expect(
    selectActivity(reversed.reduce(applyEvent, createInitialState())).entries.map(
      (entry) => entry.seq,
    ),
  ).toEqual([2, 3, 4, 5, 6]);
});

it("keeps error metadata aligned when needs-you errors are deduplicated", () => {
  const error: Extract<ServerEvent, { type: "error" }> = {
    seq: 2,
    ts: start.ts,
    type: "error" as const,
    payload: {
      source: "server",
      message: "Needs you — revision=1; head=abc123; code=review_invalid; reason=Invalid output.",
      agent_id: null,
      change_id: null,
      fatal: false,
    },
  };
  const first = [start, error].reduce(applyEvent, createInitialState());
  const duplicate = applyEvent(first, { ...error, seq: 3 });
  expect(duplicate.errors).toBe(first.errors);
  expect(duplicate.errorEvents).toBe(first.errorEvents);
  const next = applyEvent(duplicate, {
    ...error,
    seq: 4,
    payload: { ...error.payload, message: "Another error" },
  });
  expect(next.errors).toHaveLength(2);
  expect(next.errorEvents).toEqual([
    { seq: 2, ts: start.ts },
    { seq: 4, ts: start.ts },
  ]);
});

it("replaces envelope metadata with a revision and clears the previous resolution", () => {
  const before = log.reduce(applyEvent, createInitialState());
  const next = applyEvent(before, {
    ...proposal,
    seq: 7,
    ts: "2026-10-28T15:00:00Z",
    payload: { ...proposal.payload, revision: 2, head_commit: "new-head" },
  });
  const change = next.changes[proposal.payload.change_id];
  expect(change?.proposalEvent).toEqual({ seq: 7, ts: "2026-10-28T15:00:00Z" });
  expect(change?.resolution).toBeNull();
  expect(change?.resolutionEvent).toBeNull();
  expect(before.changes[proposal.payload.change_id]?.resolutionEvent).toEqual({
    seq: 5,
    ts: start.ts,
  });
});
