import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import log from "../../../fixtures/events/recorded-demo.jsonl?raw";
import { createStateStore } from "../state";
import { createReplayPlayer } from "./replayPlayer";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());
function player() {
  const store = createStateStore();
  const control = createReplayPlayer({ store, source: { text: log } });
  return { store, control };
}
describe("controlled replay", () => {
  it("seeks while playing, replaces the timer and advances the new prefix at the selected rate", async () => {
    const { store, control } = player();
    await control.ready;
    control.setSpeed(4);
    await vi.advanceTimersByTimeAsync(17);
    expect(vi.getTimerCount()).toBe(1);
    control.seek(8000);
    expect(control.getSnapshot()).toMatchObject({ position: 8000, speed: 4, status: "playing" });
    expect(vi.getTimerCount()).toBe(1);
    const prefix = store.getState().state;
    await vi.advanceTimersByTimeAsync(39);
    expect(control.getSnapshot().position).toBe(8000);
    await vi.advanceTimersByTimeAsync(1);
    expect(control.getSnapshot()).toMatchObject({ position: 8160, status: "playing" });
    await vi.advanceTimersByTimeAsync(500);
    control.pause();
    expect(control.getSnapshot().position).toBe(10160);
    expect(store.getState().state.lastSeq).toBeGreaterThan(prefix.lastSeq);
    control.dispose();
  });
  it("gives a fresh controller a new identity even when its revision matches", async () => {
    const first = player().control;
    await first.ready;
    first.dispose();
    const fresh = player().control;
    await fresh.ready;
    expect(fresh.getSnapshot().revision).toBe(first.getSnapshot().revision);
    expect(fresh.id).not.toBe(first.id);
    fresh.dispose();
  });
  it("preserves partial gaps across pause/resume and rate changes", async () => {
    const { control } = player();
    await control.ready;
    await vi.advanceTimersByTimeAsync(713);
    control.pause();
    expect(control.getSnapshot().position).toBe(713);
    await vi.advanceTimersByTimeAsync(2000);
    expect(control.getSnapshot().position).toBe(713);
    control.setSpeed(2);
    control.play();
    await vi.advanceTimersByTimeAsync(237);
    control.setSpeed(4);
    expect(control.getSnapshot().position).toBe(1187);
    await vi.advanceTimersByTimeAsync(103);
    control.pause();
    expect(control.getSnapshot().position).toBe(1599);
    control.dispose();
  });
  it("clamps, atomically rebuilds, preserves state, ends and restarts at the selected rate", async () => {
    const { store, control } = player();
    await control.ready;
    control.pause();
    const updates = vi.fn();
    store.subscribe(updates);
    control.seek(15000);
    expect(updates).toHaveBeenCalledTimes(1);
    expect(control.getSnapshot().status).toBe("paused");
    const prefix = store.getState().state;
    control.seek(-10);
    control.seek(15000);
    expect(store.getState().state).toEqual(prefix);
    control.seek(99999);
    expect(control.getSnapshot().status).toBe("ended");
    control.seek(8000);
    expect(control.getSnapshot().status).toBe("paused");
    control.setSpeed(4);
    control.restart();
    expect(control.getSnapshot()).toMatchObject({ position: 0, speed: 4, status: "playing" });
    await vi.advanceTimersByTimeAsync(6000);
    expect(control.getSnapshot()).toMatchObject({ position: 20178, status: "ended" });
    control.play();
    expect(control.getSnapshot().position).toBe(0);
    control.dispose();
  });
  it("rejects bad logs without publishing and ignores loading completed after disposal", async () => {
    const store = createStateStore();
    const broken = createReplayPlayer({
      store,
      source: { text: log.replace('"seq":2,', '"seq":99,') },
    });
    await expect(broken.ready).rejects.toThrow(/mismatch/);
    expect(store.getState().state.lastSeq).toBe(0);
    broken.dispose();
    let resolve!: (response: Response) => void;
    const pending = createReplayPlayer({
      store,
      source: { url: "/recording" },
      request: vi.fn(
        () =>
          new Promise<Response>((done) => {
            resolve = done;
          }),
      ),
    });
    pending.dispose();
    resolve(new Response(log));
    await pending.ready;
    expect(store.getState().state.lastSeq).toBe(0);
  });
  it("cancels obsolete timers and operations after disposal", async () => {
    const { control, store } = player();
    await control.ready;
    control.seek(10000);
    control.restart();
    control.dispose();
    const state = store.getState().state;
    const snapshot = control.getSnapshot();
    await vi.advanceTimersByTimeAsync(30000);
    control.seek(10000);
    control.play();
    control.restart();
    control.setSpeed(4);
    expect(store.getState().state).toBe(state);
    expect(control.getSnapshot()).toBe(snapshot);
  });
  it("ignores callbacks captured before a seek or disposal", async () => {
    const timers = vi.spyOn(globalThis, "setTimeout");
    const { control, store } = player();
    await control.ready;
    const obsolete = timers.mock.calls.at(-1)?.[0];
    control.seek(15000);
    const state = store.getState().state;
    if (typeof obsolete !== "function") throw new Error("No scheduled callback");
    obsolete();
    expect(store.getState().state).toBe(state);
    const late = timers.mock.calls.at(-1)?.[0];
    control.dispose();
    if (typeof late !== "function") throw new Error("No late callback");
    late();
    expect(store.getState().state).toBe(state);
    timers.mockRestore();
  });
  it.each(["", "{broken", "{}"])("rejects malformed recording %s", async (text) => {
    const store = createStateStore();
    const control = createReplayPlayer({ store, source: { text } });
    await expect(control.ready).rejects.toThrow();
    expect(store.getState().state.lastSeq).toBe(0);
    control.dispose();
  });
  it("rejects load errors and stops before a deferred transport import can publish", async () => {
    const store = createStateStore();
    const failed = createReplayPlayer({
      store,
      source: { url: "/recording" },
      request: vi.fn().mockResolvedValue(new Response(null, { status: 500 })),
    });
    await expect(failed.ready).rejects.toThrow("Replay log request failed: 500");
    failed.dispose();
    vi.stubEnv("MODE", "replay");
    vi.resetModules();
    try {
      const { startAppTransport } = await import("./index");
      const onReplay = vi.fn();
      const transport = startAppTransport(store, undefined, undefined, onReplay);
      transport.stop();
      await transport.ready;
      expect(onReplay).not.toHaveBeenCalled();
      expect(store.getState().state.lastSeq).toBe(0);
    } finally {
      vi.unstubAllEnvs();
      vi.resetModules();
    }
  });
});
