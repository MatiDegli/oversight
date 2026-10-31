import { describe, expect, it } from "vitest";

import { FIT_PADDING } from "./geometry";
import { panelGeometry } from "./viewFraming";

const element = (left: number, top: number, width: number, height: number) =>
  ({
    getBoundingClientRect: () => ({
      left,
      top,
      width,
      height,
      right: left + width,
      bottom: top + height,
    }),
  }) as HTMLDivElement;

describe("panelGeometry", () => {
  // A nonzero host offset catches accidental use of viewport rather than graph coordinates.
  const host = element(300, 100, 1200, 900);

  it.each([
    [84, 84],
    [24, FIT_PADDING],
  ])("reserves the clear area's top (%i px) without a cone card", (top, y1) => {
    expect(
      panelGeometry(host, {
        startAreaRef: { current: element(324, 100 + top, 1152, 800 - top) },
      }),
    ).toEqual({ area: { x1: FIT_PADDING, y1, x2: 1160, y2: 800 } });
  });

  it("keeps the existing review area beside a tall cone card", () => {
    expect(
      panelGeometry(host, {
        coneCardRef: { current: element(324, 124, 320, 500) },
        startAreaRef: { current: element(324, 640, 1152, 260) },
      }),
    ).toEqual({
      occlusion: { x1: 24, y1: 24, x2: 344, y2: 524 },
      area: { x1: 384, y1: FIT_PADDING, x2: 1160, y2: 800 },
    });
  });

  it("keeps bottom padding when the clear area extends to the host edge", () => {
    expect(
      panelGeometry(host, { startAreaRef: { current: element(324, 184, 1152, 816) } }).area,
    ).toEqual({ x1: FIT_PADDING, y1: 84, x2: 1160, y2: 860 });
  });

  it("leaves the area unspecified before the clear area is measured", () => {
    expect(panelGeometry(host, { startAreaRef: { current: null } })).toEqual({});
    expect(panelGeometry(host, { coneCardRef: { current: element(324, 124, 320, 500) } })).toEqual({
      occlusion: { x1: 24, y1: 24, x2: 344, y2: 524 },
    });
  });
});
