import { afterEach, expect, it, vi } from "vitest";

import { textWidth } from "./textWidth";

afterEach(() => vi.unstubAllGlobals());

it("keeps fallback widths uncached and caches loaded widths per font and string", () => {
  let loaded = false;
  const measureText = vi.fn((value: string) => ({ width: value === "Wi" ? 21 : 14 }));
  const context = { font: "", measureText };
  vi.stubGlobal("document", {
    fonts: { check: () => loaded },
    createElement: () => ({ getContext: () => context }),
  });
  const title = '600 17px "Test Atkinson"';
  const count = '400 16px "Test Atkinson"';
  expect(textWidth("Wi", title, 17)).toBe(2 * 17 * 0.55);
  expect(measureText).not.toHaveBeenCalled();
  loaded = true;
  expect(textWidth("Wi", title, 17)).toBe(21);
  expect(textWidth("Wi", title, 17)).toBe(21);
  expect(measureText).toHaveBeenCalledTimes(1);
  expect(textWidth("i", title, 17)).toBe(14);
  expect(textWidth("Wi", count, 16)).toBe(21);
  expect(measureText).toHaveBeenCalledTimes(3);
  expect(context.font).toBe(count);
});

it("uses the estimate when no browser font measurement exists", () => {
  vi.stubGlobal("document", undefined);
  expect(textWidth("4 methods", "400 16px sans-serif", 16)).toBe(9 * 16 * 0.55);
});
