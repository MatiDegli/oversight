import type { Page } from "@playwright/test";
import { beforeEach, expect, it, vi } from "vitest";
import { engaged } from "../../e2e/holds";

const checks = vi.hoisted(() => ({
  poll: vi.fn(() => ({ toContain: vi.fn() })),
  attribute: vi.fn(),
}));
vi.mock("@playwright/test", () => ({
  expect: Object.assign(() => ({ toHaveAttribute: checks.attribute }), { poll: checks.poll }),
}));
beforeEach(() => vi.clearAllMocks());

it.each([undefined, 60_000])("engaged preserves the caller's timeout (%s)", async (timeout) => {
  const locator = vi.fn();
  const page = { locator } as unknown as Page;
  const hold = { seq: 3, ts: "", after: "", delay: 42 };
  await engaged(page, hold, timeout);
  expect(checks.poll).toHaveBeenCalledWith(
    expect.any(Function),
    timeout === undefined ? {} : { timeout },
  );
  expect(locator).toHaveBeenCalledWith("#root");
  expect(checks.attribute).toHaveBeenCalledWith("data-seq", "2");
});
