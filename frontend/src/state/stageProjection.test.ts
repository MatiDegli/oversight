import { expect, it } from "vitest";
import log from "../../../fixtures/events/handwritten-behavior-review.jsonl?raw";
import completedLog from "../../../fixtures/events/stage-clocks.jsonl?raw";
import { parseEvent } from "../transport";
import { applyEvent } from "./applyEvent";
import { createInitialState } from "./types";
import { foldStages } from "./stageFold";

const events = log.trim().split("\n").map(parseEvent);

it.each(["2026-10-29T15:01:00.000Z", "2026-10-27T14:03:26.605Z"])(
  "retains change and graph-evidence identities across usage ticks at %s",
  (ts) => {
    const before = events.slice(0, 21).reduce(applyEvent, createInitialState());
    const tick = events.find((event) => event.type === "usage.tick");
    if (!tick) throw new Error("Missing usage tick");
    let state = before;
    for (let n = 0; n < 3; n++) {
      state = applyEvent(state, { ...tick, seq: state.lastSeq + 1, ts });
      expect(state.changes).toBe(before.changes);
      expect(state.changeFacts).toBe(before.changeFacts);
      expect(state.graph).toBe(before.graph);
      expect(state.agents).toBe(before.agents);
      expect(state.decisionEvents).toBe(before.decisionEvents);
      expect(state.clock).toBe(ts);
      expect(state.stageEvents.at(-1)?.seq).toBe(state.lastSeq);
      expect(state.changeStages["change-1"]).toEqual(foldStages(state.stageEvents, "change-1"));
    }
    expect(before.stageEvents).toHaveLength(21);
  },
);

it("retains closed stage identities on unrelated ticks and clears them on a new run", () => {
  const completed = completedLog.trim().split("\n").map(parseEvent);
  const before = completed.slice(0, 19).reduce(applyEvent, createInitialState());
  const tick = events.find((event) => event.type === "usage.tick");
  const start = completed[0];
  if (!tick || start?.type !== "run.started") throw new Error("Missing envelopes");
  const after = applyEvent(before, {
    ...tick,
    seq: before.lastSeq + 1,
    ts: "2026-10-31T00:06:00.000Z",
  });
  expect(after.changeStages).toBe(before.changeStages);
  expect(after.changeStages["change-1"]).toBe(before.changeStages["change-1"]);
  expect(after.changes).toBe(before.changes);
  const gap = applyEvent(after, { ...tick, seq: after.lastSeq + 2 });
  expect(gap.changeStages).toBe(after.changeStages);
  expect(gap.stageEvents).toBe(after.stageEvents);
  const restarted = applyEvent(after, {
    ...start,
    payload: { ...start.payload, run_id: "another-run" },
  });
  expect(restarted.changeStages).toEqual({});
  expect(restarted.changes).toEqual({});
  expect(before.changeStages["change-1"]).toEqual(foldStages(before.stageEvents, "change-1"));
});
