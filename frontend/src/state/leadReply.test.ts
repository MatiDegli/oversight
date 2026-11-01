import type { LeadReplyEvent, LeadStatusEvent } from "@contracts";
import { describe, expect, it } from "vitest";
import fixture from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { parseEvent } from "../transport";
import { applyEvent, createInitialState } from "./index";

const log = fixture.trim().split("\n").map(parseEvent);
const seeded = () => log.reduce(applyEvent, createInitialState());
const ts = "2026-11-01T17:04:57.000Z";

describe("lead thread compatibility", () => {
  it.each([null, "tool_calls", "model_calls", "time", "spend_brake"] as const)(
    "consumes reply envelopes with cut %s without adding observations or UI",
    (cut) => {
      const before = seeded();
      for (const text of ["Reply", null]) {
        // Null text without a cut is the serialized, withheld publication representation.
        const event: LeadReplyEvent = {
          seq: before.lastSeq + 1,
          ts,
          type: "lead.reply",
          payload: { agent_id: "agent-1", answers_seq: null, text, through_seq: null, cut },
        };
        const after = applyEvent(before, event);
        expect(after).toEqual({ ...before, lastSeq: event.seq, clock: ts });
        for (const key of Object.keys(before) as (keyof typeof before)[]) {
          if (key !== "lastSeq" && key !== "clock") expect(after[key]).toBe(before[key]);
        }
        expect(applyEvent(after, event)).toEqual({ ...after, gap: true });
      }
    },
  );

  it("retains idle status as existing decision evidence without lifecycle or control changes", () => {
    const before = seeded();
    const event: LeadStatusEvent = {
      seq: before.lastSeq + 1,
      ts,
      type: "lead.status",
      payload: {
        agent_id: "agent-1",
        revision: 2,
        task: "Coordinate",
        model: "fake",
        state: "idle",
        step: 1,
        detail: null,
        requested_plan_id: null,
      },
    };
    expect(applyEvent(before, event)).toEqual({
      ...before,
      lastSeq: event.seq,
      clock: ts,
      decisionEvents: [...before.decisionEvents, event],
    });
  });
});
