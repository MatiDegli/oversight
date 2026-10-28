import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { loadEditor } from "./loader";
import { prefetchEditorWhenIdle } from "./prefetch";

vi.mock("./loader", () => ({ loadEditor: vi.fn(() => Promise.resolve({})) }));
let idle: () => void;
const requestIdleCallback = vi.fn((callback: () => void) => {
  idle = callback;
  return 7;
});
const cancelIdleCallback = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("navigator", { connection: { saveData: false } });
  vi.stubGlobal("window", { requestIdleCallback, cancelIdleCallback });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("uses bounded idle work once without mounting a component", () => {
  prefetchEditorWhenIdle();
  expect(requestIdleCallback).toHaveBeenCalledWith(expect.any(Function), { timeout: 2_000 });
  expect(loadEditor).not.toHaveBeenCalled();
  idle();
  idle();
  expect(loadEditor).toHaveBeenCalledOnce();
});

it("does not schedule while saving data, and checks again before importing", () => {
  vi.stubGlobal("navigator", { connection: { saveData: true } });
  prefetchEditorWhenIdle();
  expect(requestIdleCallback).not.toHaveBeenCalled();
  vi.stubGlobal("navigator", {});
  prefetchEditorWhenIdle();
  vi.stubGlobal("navigator", { connection: { saveData: true } });
  idle();
  expect(loadEditor).not.toHaveBeenCalled();
});

it("cancels idle work on session cleanup, even if a queued callback arrives", () => {
  const cancel = prefetchEditorWhenIdle();
  cancel();
  idle();
  expect(cancelIdleCallback).toHaveBeenCalledWith(7);
  expect(loadEditor).not.toHaveBeenCalled();
});

it("falls back to a cancellable timeout when idle callbacks are unavailable", () => {
  vi.useFakeTimers();
  vi.stubGlobal("window", { setTimeout, clearTimeout });
  const cancel = prefetchEditorWhenIdle();
  cancel();
  vi.runAllTimers();
  expect(loadEditor).not.toHaveBeenCalled();
  prefetchEditorWhenIdle();
  vi.advanceTimersByTime(199);
  expect(loadEditor).not.toHaveBeenCalled();
  vi.advanceTimersByTime(1);
  expect(loadEditor).toHaveBeenCalledOnce();
});

it("contains a speculative import failure until an explicit open handles it", async () => {
  vi.mocked(loadEditor).mockRejectedValueOnce(new Error("offline"));
  prefetchEditorWhenIdle();
  idle();
  await Promise.resolve();
  expect(loadEditor).toHaveBeenCalledOnce();
});
