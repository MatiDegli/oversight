import { expect, it, vi } from "vitest";
import log from "../../../fixtures/events/stage-clocks.jsonl?raw";
import { applyEvent, createInitialState, createStateStore } from "../state";
import { parseEvent } from "./events";
import { createReplayPlayer } from "./replayPlayer";
import { startLive, type EventStream } from "./live";

it("reconstructs identical live and replay clocks through exact seeking and restart", async () => {
  vi.useFakeTimers();
  const store = createStateStore();
  const replay = createReplayPlayer({ store, source: { text: log } });
  const liveStore = createStateStore();
  const stream: EventStream = {
    readyState: 1,
    onmessage: null,
    onerror: null,
    onopen: null,
    close: () => {},
  };
  const live = startLive({ store: liveStore, openStream: () => stream });
  try {
    await replay.ready;
    replay.pause();
    const events = log.trim().split("\n").map(parseEvent);
    let state = createInitialState();
    for (const event of events) {
      state = applyEvent(state, event);
      stream.onmessage?.({ data: JSON.stringify(event) } as MessageEvent<string>);
      replay.seekSeq(event.seq);
      expect(store.getState().state).toEqual(state);
      expect(liveStore.getState().state).toEqual(state);
    }
    replay.seekSeq(14);
    const prefix = store.getState().state;
    expect(prefix.changeStages["change-1"]?.waiting_for_person.open).toBe(true);
    await vi.advanceTimersByTimeAsync(100000);
    expect(store.getState().state).toBe(prefix);
    replay.restart();
    replay.pause();
    expect(store.getState().state.changes).toEqual({});
    replay.seekSeq(14);
    expect(store.getState().state).toEqual(prefix);
    replay.seekSeq(events.length);
    expect(store.getState().state).toEqual(liveStore.getState().state);
  } finally {
    live.stop();
    replay.dispose();
    vi.useRealTimers();
  }
});
