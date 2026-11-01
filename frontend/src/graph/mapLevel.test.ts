import type { GraphSnapshot } from "@contracts";
import { expect, it } from "vitest";
import demo from "../../../fixtures/graph/demo-repo.json";
import { createInitialState } from "../state";
import { graphElements } from "./elements";
import { readBoltonsSnapshot } from "./exactFixtures.testSupport";
import { MAP_ENTER, MAP_LEAVE, MAP_MIN_MODULES, nextLevel, targetLevel } from "./mapLevel";

it("uses strict thresholds and preserves both levels in the hysteresis band", () => {
  expect(nextLevel("cards", MAP_ENTER, 24)).toBe("cards");
  expect(nextLevel("cards", MAP_ENTER - 0.0001, 24)).toBe("map");
  expect(nextLevel("map", MAP_LEAVE, 24)).toBe("map");
  expect(nextLevel("map", MAP_LEAVE + 0.0001, 24)).toBe("cards");
  for (const zoom of [MAP_ENTER, 0.66, MAP_LEAVE]) {
    expect(nextLevel("map", zoom, 24)).toBe("map");
    expect(nextLevel("cards", zoom, 24)).toBe("cards");
  }
});
it("decides explicit targets independently of the current level", () => {
  expect(targetLevel(MAP_LEAVE, 24)).toBe("cards");
  expect(targetLevel(0.646, 24)).toBe("map");
  expect(targetLevel(0.648, 24)).toBe("cards");
});
it.each([23, 24])("requires at least 24 shown modules (%i)", (shown) => {
  expect(MAP_MIN_MODULES).toBe(24);
  for (const zoom of [0.2, 0.646, 0.66, MAP_LEAVE, 0.7]) {
    expect(nextLevel("cards", zoom, shown)).toBe(
      shown === 24 && zoom < MAP_ENTER ? "map" : "cards",
    );
    expect(nextLevel("map", zoom, shown)).toBe(shown === 24 && zoom <= MAP_LEAVE ? "map" : "cards");
    expect(targetLevel(zoom, shown)).toBe(shown === 24 && zoom < MAP_ENTER ? "map" : "cards");
  }
});
function shownModules(graph: GraphSnapshot, showTests: boolean) {
  return graphElements({ ...createInitialState(), graph }, showTests).filter(
    (e) => e.data.kind === "module",
  ).length;
}
it.each([false, true])("keeps the demo on cards after the tests filter (%s)", (showTests) => {
  const shown = shownModules(demo as unknown as GraphSnapshot, showTests);
  expect(shown).toBe(showTests ? 12 : 6);
  expect(nextLevel("map", 0.2, shown)).toBe("cards");
  expect(targetLevel(0.2, shown)).toBe("cards");
});
const boltons = readBoltonsSnapshot();
it.skipIf(!boltons).each([false, true])(
  "keeps Boltons eligible after the tests filter (%s)",
  (showTests) => {
    if (!boltons) throw new Error("Missing optional snapshot");
    const shown = shownModules(boltons, showTests);
    expect(shown).toBe(showTests ? 66 : 34);
    expect(nextLevel("cards", 0.2, shown)).toBe("map");
    expect(targetLevel(0.2, shown)).toBe("map");
  },
);
