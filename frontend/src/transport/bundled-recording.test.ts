import { describe, expect, it } from "vitest";

import recorded from "../../../fixtures/events/recorded-demo.jsonl?raw";
import provenance from "../../../fixtures/events/recorded-demo.jsonl.provenance.json";
import { createInitialState, applyEvent, selectAgentCard } from "../state";
import { replaySource } from "./index";
import { parseEvent } from "./events";

describe("the public replay recording", () => {
  it("defaults to the exact exported real run and its receipt", () => {
    expect(replaySource.text).toBe(recorded);
    expect(replaySource.kind).toBe("real recorded run");
    const events = recorded.trim().split("\n").map(parseEvent);
    expect(events).toHaveLength(provenance.event_count);
    expect(events[0]).toMatchObject({
      type: "run.started",
      payload: { run_id: provenance.run_id },
    });
  });

  it("replays both approved merges and shows the recorded model names", () => {
    const events = recorded.trim().split("\n").map(parseEvent);
    const state = events.reduce(applyEvent, createInitialState());
    expect(Object.keys(state.agents)).toHaveLength(2);
    expect(selectAgentCard(state, "agent-1")?.modelLabel).toBe("Muse Spark");
    expect(selectAgentCard(state, "agent-2")?.modelLabel).toBe("Muse Spark Open");
    const decisions = events.filter((event) => event.type === "change.resolved");
    expect(decisions).toHaveLength(2);
    expect(
      decisions.every(
        (event) => event.payload.decision === "approved" && event.payload.merge === "merged",
      ),
    ).toBe(true);
    expect(state.lastSeq).toBe(provenance.event_count);
    expect(state.gap).toBe(false);
  });
});
