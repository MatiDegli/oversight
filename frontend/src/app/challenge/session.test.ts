import { afterEach, expect, it, vi } from "vitest";
import { createInitialState, stateStore } from "../../state";
import { startAppTransport } from "../../transport";
import { startAppSession } from "../session";
import { freezePractice } from "./session";
import { manifest, validateManifest } from "./manifest";
import { recording } from "./recording.fixture";

vi.mock("../../graph", () => ({ afterFirstGraphDraw: () => vi.fn() }));
vi.mock("../../editor", () => ({ prefetchEditorWhenIdle: () => vi.fn() }));
vi.mock("../../transport", async (original) => {
  const actual = await original<typeof import("../../transport")>();
  return {
    ...actual,
    startAppTransport: vi.fn((store: Parameters<typeof actual.startAppTransport>[0]) =>
      actual.startReplay({ store, source: recording }),
    ),
  };
});
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});
it("revokes abandoned callbacks, freezes 63, restarts normal replay and disposes on unmount", async () => {
  vi.useFakeTimers();
  const root = { dataset: {} };
  const report = vi.fn();
  const stop = startAppSession(root, report);
  await vi.advanceTimersByTimeAsync(1000);
  const abandoned = vi.mocked(startAppTransport).mock.calls[0]?.[0];
  if (!abandoned) throw new Error("No session");
  const staleApply = abandoned.getState().applyEvents;
  const bound = validateManifest(manifest, recording);
  freezePractice(stop, bound.cut, root);
  const frozen = stateStore.getState().state;
  await vi.advanceTimersByTimeAsync(120_000);
  staleApply(bound.events.slice(63));
  abandoned.setState({ state: createInitialState() });
  expect(stateStore.getState().state).toBe(frozen);
  expect(root.dataset).toMatchObject({ seq: "63" });
  expect(Object.values(frozen.changes).map((change) => change.resolution)).toEqual([null, null]);
  const cleanup = startAppSession(root, report);
  await vi.advanceTimersByTimeAsync(120_000);
  expect(stateStore.getState().state.lastSeq).toBe(69);
  expect(
    Object.values(stateStore.getState().state.changes).every(
      (change) => change.status === "approved",
    ),
  ).toBe(true);
  cleanup();
  const calls = report.mock.calls.length;
  const cursor = { ...root.dataset };
  stateStore.setState({ state: createInitialState() });
  await vi.runAllTimersAsync();
  expect(report).toHaveBeenCalledTimes(calls);
  expect(root.dataset).toEqual(cursor);
});
