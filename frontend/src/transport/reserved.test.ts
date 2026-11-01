import { describe, expect, it } from "vitest";

import reserved from "../../../fixtures/contracts/reserved-1.3.json";
import fixture from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { applyEvent, createInitialState, createStateStore } from "../state";
import { parseEvent } from "./events";
import { startReplay } from "./replay";

const log = fixture.trim().split("\n").map(parseEvent);

describe("reserved level 3 events", () => {
  it.each(reserved)("accepts $type and retains inbox evidence without commands", (example) => {
    const state = log.reduce(applyEvent, createInitialState());
    const event = parseEvent(JSON.stringify({ ...example, seq: state.lastSeq + 1 }));
    expect(event.type).toBe(example.type);
    const next = applyEvent(state, event);
    expect(next).toEqual({
      ...state,
      mergeMode: event.type === "merge_mode.changed" ? event.payload : state.mergeMode,
      lastSeq: event.seq,
      clock: event.ts,
      gap: false,
      decisionEvents: [
        ...state.decisionEvents,
        ...(event.type === "change.assessed" || event.type === "plan.updated" ? [event] : []),
      ],
    });
    const gap = applyEvent(state, { ...event, seq: event.seq + 1 });
    expect(gap).toEqual({ ...state, gap: true });
  });

  it("replays the reserved events without creating plans, agents or changes", async () => {
    const state = log.reduce(applyEvent, createInitialState());
    const additions = reserved.map((example, index) => ({
      ...example,
      seq: state.lastSeq + index + 1,
    }));
    const store = createStateStore();
    const replay = startReplay({
      store,
      source: { text: [...log, ...additions].map((event) => JSON.stringify(event)).join("\n") },
      speed: 0,
    });
    await replay.ready;
    expect(store.getState().state).toEqual({
      ...state,
      mergeMode: { mode: "merge_when_safe", revision: 1 },
      lastSeq: state.lastSeq + additions.length,
      clock: additions.at(-1)?.ts,
      decisionEvents: [
        ...state.decisionEvents,
        ...additions.filter(
          (event) => event.type === "change.assessed" || event.type === "plan.updated",
        ),
      ],
    });
    replay.stop();
  });
});
