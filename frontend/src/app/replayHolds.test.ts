import type { Page } from "@playwright/test";
import { beforeEach, expect, it, vi } from "vitest";
import { engaged } from "../../e2e/holds";

const mocks = vi.hoisted(() => ({ poll: vi.fn(), contains: vi.fn(), attribute: vi.fn() }));
vi.mock("@playwright/test", () => ({
  expect: Object.assign(() => ({ toHaveAttribute: mocks.attribute }), {
    poll: mocks.poll,
  }),
}));
beforeEach(() => {
  vi.clearAllMocks();
  mocks.poll.mockReturnValue({ toContain: mocks.contains });
});

it.each([undefined, 60_000])("uses Playwright's default unless timeout is %s", async (timeout) => {
  const page = { evaluate: vi.fn(), locator: vi.fn() } as unknown as Page;
  const hold = { seq: 8, ts: "", after: "", delay: 1234 };
  await engaged(page, hold, timeout);
  expect(mocks.poll).toHaveBeenCalledExactlyOnceWith(expect.any(Function), {
    ...(timeout === undefined ? {} : { timeout }),
  });
  expect(mocks.contains).toHaveBeenCalledExactlyOnceWith(hold.delay);
  expect(mocks.attribute).toHaveBeenCalledExactlyOnceWith("data-seq", "7");
});
