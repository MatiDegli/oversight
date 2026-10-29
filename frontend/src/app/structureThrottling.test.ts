import type { Page } from "@playwright/test";
import { expect, it, vi } from "vitest";
import { withCpuThrottling } from "./structure.browser.support";

function fixture() {
  const calls: unknown[] = [];
  const send = vi.fn((method: string, params: unknown) => {
    calls.push([method, params]);
    return Promise.resolve();
  });
  const detach = vi.fn(() => {
    calls.push("detach");
    return Promise.resolve();
  });
  const page = {
    context: () => ({ newCDPSession: () => Promise.resolve({ send, detach }) }),
  } as unknown as Page;
  return { page, calls, send };
}

it.each([1, 4, 6])("resets CPU rate %s and detaches before returning", async (rate) => {
  const { page, calls } = fixture();
  await withCpuThrottling(page, rate, () => {
    calls.push("drag");
    return Promise.resolve();
  });
  expect(calls).toEqual([
    ["Emulation.setCPUThrottlingRate", { rate }],
    "drag",
    ["Emulation.setCPUThrottlingRate", { rate: 1 }],
    "detach",
  ]);
});

it.each(["setup", "drag"])("cleans up when %s fails and propagates the failure", async (phase) => {
  const { page, calls, send } = fixture();
  const failure = new Error(`${phase} failed`);
  if (phase === "setup") send.mockRejectedValueOnce(failure);
  const run = vi.fn(() => Promise.reject(failure));
  await expect(withCpuThrottling(page, 6, run)).rejects.toBe(failure);
  expect(calls.slice(-2)).toEqual([["Emulation.setCPUThrottlingRate", { rate: 1 }], "detach"]);
  expect(run).toHaveBeenCalledTimes(phase === "setup" ? 0 : 1);
});

it("still detaches if resetting the CPU rate fails", async () => {
  const { page, calls, send } = fixture();
  const failure = new Error("reset failed");
  send.mockResolvedValueOnce(undefined).mockRejectedValueOnce(failure);
  await expect(withCpuThrottling(page, 4, () => Promise.resolve())).rejects.toBe(failure);
  expect(calls.at(-1)).toBe("detach");
});
