import { expect, it } from "vitest";
import { headerCases, headerState } from "./ReviewHeader.fixture";
import { comparisonPreview } from "./reviewBodyEvidence";

it.each([
  ["a", "24 inputs, 2 differ · 1 of 36 functions"],
  ["b", "10 inputs, 9 differ · 6 callers not measured"],
  ["c", "2 of 9 callers measured · 2 of 36 functions"],
  ["merged", "24 inputs, 1 differ · 1 of 36 functions"],
  ["d", ""],
] as const)("previews %s from evidence", (name, expected) => {
  expect(comparisonPreview(headerState(name), headerCases[name].changeId)).toBe(expected);
});
