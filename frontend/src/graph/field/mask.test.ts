import { expect, it, vi } from "vitest";
import { contains, overlayBoxes, unmaskedPath } from "./mask";

it("reads overlay rectangles relative to the canvas, omits hidden and nested children, and deduplicates", () => {
  const overlay = (left: number, top: number, width: number, height: number, nested = false) => ({
    getBoundingClientRect: vi.fn(() => ({
      left,
      top,
      right: left + width,
      bottom: top + height,
      width,
      height,
    })),
    parentElement: { closest: () => (nested ? {} : null) },
  });
  const title = overlay(110, 90, 120, 40);
  const legend = overlay(110, 200, 150, 60);
  const zoom = overlay(400, 200, 40, 90);
  const hidden = overlay(0, 0, 0, 0);
  const nested = overlay(120, 90, 10, 10, true);
  const dialog = overlay(250, 100, 100, 100);
  const canvas = {
    closest: () => ({ querySelectorAll: () => [title, legend, zoom, hidden, nested] }),
    getBoundingClientRect: () => ({ left: 100, top: 80 }),
    ownerDocument: { querySelectorAll: () => [dialog, title] },
  } as unknown as HTMLCanvasElement;
  const masks = overlayBoxes(canvas);
  expect(masks).toEqual([
    { x1: 10, y1: 10, x2: 130, y2: 50 },
    { x1: 10, y1: 120, x2: 160, y2: 180 },
    { x1: 300, y1: 120, x2: 340, y2: 210 },
    { x1: 150, y1: 20, x2: 250, y2: 120 },
  ]);
  expect(contains(masks, 20, 20)).toBe(true);
  expect(contains(masks, 0, 0)).toBe(false);
  expect(title.getBoundingClientRect).toHaveBeenCalledOnce();
  expect(nested.getBoundingClientRect).not.toHaveBeenCalled();
});

it("clips travelling markers by the union of overlapping overlay masks", () => {
  expect(
    unmaskedPath(
      [
        { x1: 0, y1: 0, x2: 50, y2: 60 },
        { x1: 0, y1: 40, x2: 50, y2: 80 },
      ],
      100,
      100,
    ),
  ).toBe("M0,80H50V100H0ZM50,0H100V100H50Z");
});
