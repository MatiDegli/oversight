import { expect, it } from "vitest";
import { headerCases, headerState } from "./ReviewHeader.fixture";
import { comparisonPreview, selectReviewBody } from "./reviewBodyEvidence";

it.each([
  ["a", "24 inputs, 2 differ · 1 of 36 functions"],
  ["b", "10 inputs, 9 differ · 6 callers not measured"],
  ["c", "2 of 9 callers measured · 2 of 36 functions"],
  ["merged", "24 inputs, 1 differ · 1 of 36 functions"],
  ["d", ""],
] as const)("previews %s from evidence", (name, expected) => {
  expect(comparisonPreview(headerState(name), headerCases[name].changeId)).toBe(expected);
});

it("returns no preview parts for an eligible merged review without measurements", () => {
  const state = headerState("merged");
  const { changeId } = headerCases.merged;
  const proposal = state.changes[changeId]?.proposal;
  if (!proposal) throw new Error("Missing merged proposal");
  state.changeFacts[changeId] = [];
  proposal.impact.entries = [];
  expect(selectReviewBody(state, changeId).kind).toBe("g");
  expect(comparisonPreview(state, changeId)).toBe("");
});
