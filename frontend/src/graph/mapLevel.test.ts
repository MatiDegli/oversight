import { expect, it } from "vitest";
import { MAP_ENTER, MAP_LEAVE, nextLevel, targetLevel } from "./mapLevel";

it("uses strict thresholds and preserves both levels in the hysteresis band", () => {
  expect(nextLevel("cards", MAP_ENTER)).toBe("cards");
  expect(nextLevel("cards", MAP_ENTER - 0.0001)).toBe("map");
  expect(nextLevel("map", MAP_LEAVE)).toBe("map");
  expect(nextLevel("map", MAP_LEAVE + 0.0001)).toBe("cards");
  for (const zoom of [MAP_ENTER, 0.66, MAP_LEAVE]) {
    expect(nextLevel("map", zoom)).toBe("map");
    expect(nextLevel("cards", zoom)).toBe("cards");
  }
});
it("decides explicit targets independently of the current level", () => {
  expect(targetLevel(MAP_LEAVE)).toBe("cards");
  expect(targetLevel(0.646)).toBe("map");
  expect(targetLevel(0.648)).toBe("cards");
});
