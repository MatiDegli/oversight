import { expect, it } from "vitest";
import { elapsed, toolTime, causeWords, budgetWords, timeWords } from "./progressWords";
import { progressState } from "./progressFixture";

it("uses event elapsed and tool durations with their specified precision", () => {
  expect([0, 59_999, 60_000, 240_000, 331_000].map(elapsed)).toEqual([
    "0 s",
    "59 s",
    "1 min 00 s",
    "4 min 00 s",
    "5 min 31 s",
  ]);
  expect([1, 999, 1000, 12_345, 60_000].map(toolTime)).toEqual([
    "1 ms",
    "999 ms",
    "1.0 s",
    "12.3 s",
    "1 min 00 s",
  ]);
  expect(Object.values(causeWords)).toEqual([
    "Rate limited",
    "Server error",
    "Connection failed",
    "Timed out",
    "Refused",
  ]);
  expect(timeWords(485_000, 600)).toEqual({ used: "8 min", bound: "10 min" });
  expect(timeWords(80_000, 100).bound).toBe("100 s");
});
it("uses configured budget thresholds, metered Meta words and actual blocked percentage", () => {
  const state = progressState("limits"),
    budget = state.run?.budget,
    mark = state.budgetMarks[0];
  if (!budget || !mark) throw new Error("Missing budget");
  expect(budgetWords({ ...mark, brake: "warn_50", spent_usd: 75 }, budget, false)).toEqual({
    lead: "Budget at 50%",
    detail: "US$75.00 of US$150 · new Meta runs stop at 90%",
  });
  expect(budgetWords(mark, budget, false)).toEqual({
    lead: "Budget at 80%",
    detail: "US$120.04 of US$150 · new Meta runs stop at 90%",
  });
  expect(budgetWords({ ...mark, brake: "blocked", spent_usd: 136 }, budget, false)).toEqual({
    lead: "Budget at 90%",
    detail: "US$136.00 of US$150 · new Meta runs are refused",
  });
  expect(budgetWords(mark, budget, true).detail).toBe("new Meta runs stop at 90%");
});

it("omits a blocked percentage and amounts when spend is unknown", () => {
  const state = progressState("limits");
  const budget = state.run?.budget,
    mark = state.budgetMarks[0];
  if (!budget || !mark) throw new Error("Missing budget");
  expect(
    budgetWords({ ...mark, brake: "blocked", spent_usd: 136, budget_usd: 150 }, budget, true),
  ).toEqual({
    lead: "Budget",
    detail: "new Meta runs are refused",
  });
});

it("omits a blocked percentage without a positive budget denominator", () => {
  const state = progressState("limits");
  const budget = state.run?.budget,
    mark = state.budgetMarks[0];
  if (!budget || !mark) throw new Error("Missing budget");
  for (const spent_usd of [0, 10]) {
    for (const unknown of [false, true]) {
      expect(
        budgetWords({ ...mark, brake: "blocked", spent_usd, budget_usd: 0 }, budget, unknown).lead,
      ).toBe("Budget");
    }
  }
});

it.each([
  ["warn_50", 29],
  ["warn_80", 57],
] as const)("rounds configured %s and stop thresholds to whole percentages", (brake, percent) => {
  const state = progressState("limits");
  const budget = state.run?.budget,
    mark = state.budgetMarks[0];
  if (!budget || !mark) throw new Error("Missing budget");
  expect(
    budgetWords({ ...mark, brake }, { ...budget, warn_at: [0.29, 0.57], block_at: 0.58 }, true),
  ).toEqual({
    lead: `Budget at ${String(percent)}%`,
    detail: "new Meta runs stop at 58%",
  });
});
