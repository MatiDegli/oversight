import { afterEach, expect, it, vi } from "vitest";
import type { ServerEvent } from "@contracts";
import fixture from "../../../fixtures/views/reference-cases.json";
import { createStateStore } from "../state";
// eslint-disable-next-line no-restricted-imports -- Exercise the production replay controller with synthetic events.
import { createReplayPlayer } from "../transport/replayPlayer";
import { createViewHistory } from "./viewHistory";

afterEach(() => vi.useRealTimers());
it("pauses before saving Chat and returns to the exact offset and sequence, paused", async () => {
  vi.useFakeTimers();
  const store = createStateStore();
  const events = (fixture.events as ServerEvent[]).map((event) => ({
    ...event,
    ts: required(fixture.events[0]).ts,
  }));
  const player = createReplayPlayer({
    store,
    source: { text: events.map((event) => JSON.stringify(event)).join("\n") },
  });
  await player.ready;
  player.seekSeq(12, 0);
  const chat = store.getState().state;
  const history = createViewHistory(player, () => store.getState().state);
  history.subscribe(() => {});
  history.open(6);
  expect(history.getSnapshot()).toMatchObject({ position: 0, lastSeq: 12, chat, eventSeq: 6 });
  expect(history.getSnapshot()?.chat).toBe(chat);
  expect(store.getState().state.lastSeq).toBe(6);
  history.open(8);
  expect(history.getSnapshot()?.chat).toBe(chat);
  history.back();
  expect(store.getState().state).toEqual(chat);
  expect(player.getSnapshot()).toMatchObject({ position: 0, status: "paused" });
  expect(history.getSnapshot()).toBeNull();
  history.dispose();
  player.dispose();
});
it("ordinary seek, playback, restart and disposal cancel the Chat override", async () => {
  vi.useFakeTimers();
  const store = createStateStore();
  const player = createReplayPlayer({
    store,
    source: { text: fixture.events.map((event) => JSON.stringify(event)).join("\n") },
  });
  await player.ready;
  player.seekSeq(15);
  const history = createViewHistory(player, () => store.getState().state);
  history.subscribe(() => {});
  for (const action of [
    () => {
      player.seek(0);
    },
    player.play,
    player.restart,
  ]) {
    history.open(6);
    action();
    expect(history.getSnapshot()).toBeNull();
    player.seekSeq(15);
  }
  history.open(999);
  expect(history.getSnapshot()).toBeNull();
  history.open(6);
  history.dispose();
  expect(history.getSnapshot()).toBeNull();
  player.dispose();
});
it("saves a partial time offset after pause has published the current prefix", async () => {
  vi.useFakeTimers();
  const store = createStateStore();
  const events = fixture.events.map((event, i) => ({
    ...event,
    ts: new Date(Date.parse(event.ts) + i * 1000).toISOString(),
  }));
  const player = createReplayPlayer({
    store,
    source: { text: events.map((event) => JSON.stringify(event)).join("\n") },
  });
  await player.ready;
  await vi.advanceTimersByTimeAsync(657);
  const history = createViewHistory(player, () => store.getState().state);
  history.subscribe(() => {});
  history.open(1);
  expect(history.getSnapshot()).toMatchObject({ position: 657, lastSeq: 1 });
  history.back();
  expect(player.getSnapshot()).toMatchObject({ position: 657, status: "paused" });
  expect(store.getState().state.lastSeq).toBe(1);
  history.dispose();
  player.dispose();
});

function required<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("Missing synthetic fixture value");
  return value;
}

it("publishes the saved Chat before replacing the reducer and retains it until Back restores", async () => {
  vi.useFakeTimers();
  const store = createStateStore();
  const player = createReplayPlayer({
    store,
    source: { text: fixture.events.map((event) => JSON.stringify(event)).join("\n") },
  });
  await player.ready;
  player.seekSeq(15);
  const history = createViewHistory(player, () => store.getState().state);
  history.subscribe(() => {});
  const observed: boolean[] = [];
  const unsubscribe = store.subscribe(() => {
    observed.push(history.getSnapshot() !== null);
  });
  history.open(6);
  history.back();
  expect(observed).toEqual([true, true]);
  expect(history.getSnapshot()).toBeNull();
  unsubscribe();
  history.dispose();
  player.dispose();
});
