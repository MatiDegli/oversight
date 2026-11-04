import type { LeadView, PreviewStatusEvent, ServerEvent } from "@contracts";
import { describe, expect, it, vi } from "vitest";
import fixture from "../../../fixtures/views/reference-cases.json";
import { applyEvent, createInitialState, createStateStore } from "./index";
import { resolveViewReferences } from "./viewReferences";
import { parseEvent, startLive, startReplay } from "../transport";
import type { EventStream } from "../transport";

class FakeSource implements EventStream {
  onmessage: EventStream["onmessage"] = null;
  onerror: EventStream["onerror"] = null;
  readyState = 1;
  close = vi.fn();
  emit(event: ServerEvent) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(event) }));
  }
}

// Existing fixtures remain unchanged; preview envelopes here are scripted test data.
const events = fixture.events.map((event) => parseEvent(JSON.stringify(event)));
const status: PreviewStatusEvent = {
  seq: events.length + 1,
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
};
const request: LeadView = {
  agent_id: "agent-9",
  through_seq: events.length,
  caption: null,
  view: { kind: "preview", agent_id: "agent-1" },
};

describe("preview contract readers", () => {
  it.each([0, 1, 6, 15])("refuses unavailable proposals at prefix %i", (count) => {
    const prefix = events.slice(0, count);
    const state = prefix.reduce(applyEvent, createInitialState());
    expect(resolveViewReferences(state, { ...request, through_seq: count })).toEqual([]);
  });

  it("resolves an eligible retained suggestion in live and replay folds", () => {
    const prefix = events.slice(0, 6);
    const agent = prefix.find((event) => event.type === "agent.status");
    if (agent?.type !== "agent.status") throw new Error("Missing agent");
    const eligible: ServerEvent[] = [
      ...prefix,
      { ...agent, seq: 7, payload: { ...agent.payload, state: "ready_for_review" } },
      { type: "lead.view", seq: 8, ts: status.ts, payload: { ...request, through_seq: 7 } },
    ];
    const state = eligible.reduce(applyEvent, createInitialState());
    const retained = state.leadViews[request.agent_id]?.[0];
    expect(retained).toBeDefined();
    if (retained) expect(resolveViewReferences(state, retained.payload)).toEqual(["change-1"]);
  });

  it("registers preview.status and advances sequence, clock and open stage horizons only", () => {
    const before = events.reduce(applyEvent, createInitialState());
    const parsed = parseEvent(JSON.stringify(status));
    expect(parsed).toEqual(status);
    const after = applyEvent(before, parsed);
    const prior = before.changeStages["change-1"];
    if (!prior) throw new Error("Missing stage readings");
    expect(after).toEqual({
      ...before,
      lastSeq: status.seq,
      clock: status.ts,
      gap: false,
      stageEvents: [...before.stageEvents, status],
      changeStages: {
        ...before.changeStages,
        "change-1": {
          ...prior,
          agent_work: { ...prior.agent_work, ms: 1000, known_ms: 1000 },
          waiting_for_person: { ...prior.waiting_for_person, known_ms: 1000 },
        },
      },
    });
    for (const key of Object.keys(before)) {
      if (!["lastSeq", "clock", "gap", "stageEvents", "changeStages"].includes(key))
        expect(after[key as keyof typeof after]).toBe(before[key as keyof typeof before]);
    }
    expect(resolveViewReferences(after, request)).toEqual([]);
    expect(applyEvent(before, { ...status, seq: status.seq + 1 }).gap).toBe(true);
    expect(applyEvent(createInitialState(), { ...status, seq: 1 }).gap).toBe(true);
  });

  it("preserves live/replay parity and rebuilds accepted prefixes without preview state", async () => {
    const store = createStateStore();
    const source = new FakeSource();
    const onError = vi.fn();
    const live = startLive({ store, openStream: () => source, onError });
    const prefix: ServerEvent[] = [...events, status];
    try {
      await live.ready;
      for (const event of prefix) source.emit(event);
      expect(onError).not.toHaveBeenCalled();
      const expected = prefix.reduce(applyEvent, createInitialState());
      expect(store.getState().state).toEqual(expected);
      for (const count of [1, 6, 15, prefix.length]) {
        const displayed = prefix.slice(0, count);
        const replayStore = createStateStore();
        const replay = startReplay({
          store: replayStore,
          speed: 0,
          source: { text: displayed.map((event) => JSON.stringify(event)).join("\n") },
        });
        try {
          await replay.ready;
          expect(replayStore.getState().state).toEqual(
            displayed.reduce(applyEvent, createInitialState()),
          );
          expect(resolveViewReferences(replayStore.getState().state, request)).toEqual([]);
        } finally {
          replay.stop();
        }
      }
    } finally {
      live.stop();
    }
  });
});
