import { describe, expect, it } from "vitest";
import type { LeadView, ServerEvent } from "@contracts";
import fixture from "../../../fixtures/views/reference-cases.json";
import { applyEvent } from "./applyEvent";
import { createInitialState } from "./types";
import { MOMENT_EVENT_TYPES, resolveViewReferences } from "./viewReferences";
import { parseEvent } from "../transport";

const events = fixture.events as unknown as ServerEvent[];

describe("shared reference resolution", () => {
  it("uses the shared milestone catalog", () => {
    expect([...MOMENT_EVENT_TYPES].sort()).toEqual([...fixture.moment_event_types].sort());
  });
  for (const sample of fixture.cases)
    it(sample.name, () => {
      const prefix = events.slice(0, sample.prefix);
      const state = prefix.reduce(applyEvent, createInitialState());
      expect(state.gap).toBe(false);
      expect(resolveViewReferences(state, sample.request as LeadView)).toEqual(sample.expected);
    });

  it("parses and stores ordered views per lead, then resets on run.started", () => {
    let state = events.reduce(applyEvent, createInitialState());
    const before = state;
    const sample = fixture.cases[0];
    const first = events[0];
    if (!sample || !first) throw new Error("Reference fixture must contain events and cases");
    for (let i = 1; i <= 3; i++) {
      const payload = { ...sample.request, agent_id: i === 2 ? "agent-8" : "agent-9" };
      state = applyEvent(
        state,
        parseEvent(
          JSON.stringify({
            seq: events.length + i,
            ts: first.ts,
            type: "lead.view",
            payload,
          }),
        ),
      );
    }
    expect(before.leadViews).toEqual({});
    expect(state.leadViews["agent-9"]?.map((e) => e.seq)).toEqual([16, 18]);
    expect(state.leadViews["agent-8"]).toHaveLength(1);
    state = applyEvent(state, {
      ...first,
      seq: 1,
      payload: {
        ...first.payload,
        run_id: "run-20261103-010000",
      },
    } as ServerEvent);
    expect(state.leadViews).toEqual({});
  });
});
