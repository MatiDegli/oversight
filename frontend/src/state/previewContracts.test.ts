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

describe("dormant preview contract readers", () => {
  it.each([0, 1, 6, 15])("resolves nothing at prefix %i", (count) => {
    const prefix = events.slice(0, count);
    const state = prefix.reduce(applyEvent, createInitialState());
    expect(resolveViewReferences(state, { ...request, through_seq: count }, prefix)).toEqual([]);
  });

  it("registers preview.status and changes only sequence, gap and clock bookkeeping", () => {
    const before = events.reduce(applyEvent, createInitialState());
    const parsed = parseEvent(JSON.stringify(status));
    expect(parsed).toEqual(status);
    const after = applyEvent(before, parsed);
    expect(after).toEqual({ ...before, lastSeq: status.seq, clock: status.ts, gap: false });
    for (const key of Object.keys(before)) {
      if (key !== "lastSeq" && key !== "clock" && key !== "gap")
        expect(after[key as keyof typeof after]).toBe(before[key as keyof typeof before]);
    }
    expect(resolveViewReferences(after, request, [...events, status])).toEqual([]);
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
          expect(resolveViewReferences(replayStore.getState().state, request, displayed)).toEqual(
            [],
          );
        } finally {
          replay.stop();
        }
      }
    } finally {
      live.stop();
    }
  });
});
