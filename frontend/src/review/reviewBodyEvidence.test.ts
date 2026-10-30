import { expect, it } from "vitest";
import { headerState } from "./ReviewHeader.fixture";
import { availableFacts, firstCaller, withMeasuredFacts } from "./measuredCallers.fixture";
import { selectCallerComparison, selectReviewBody, smallestExample } from "./reviewBodyEvidence";

it("counts measured callers separately from possibly affected callers", () => {
  const view = selectCallerComparison(headerState("b"), "change-1");
  expect([view.differing.length, view.measured, view.total]).toEqual([1, 3, 9]);
  expect(view.same).toEqual([
    "Cart.subtotals and Customer.quote: same result on 12 recorded calls each.",
  ]);
});
it("leaves a caller without a measurement unmeasured", () => {
  const view = selectCallerComparison(
    withMeasuredFacts({ ...availableFacts, measured_cone: [firstCaller] }),
    "change-1",
  );
  expect(view.measured).toBe(1);
  expect(view.rows.filter((r) => r.evidence === null)).toHaveLength(8);
});
it("never counts an unstable observation as a differing caller", () => {
  const state = withMeasuredFacts({
    ...availableFacts,
    measured_cone: [
      { ...firstCaller, rows: firstCaller.rows.map((r) => ({ ...r, unstable: true })) },
    ],
  });
  expect(selectCallerComparison(state, "change-1").differing).toEqual([]);
});
it("uses the smallest retained stable differing example and escapes it at render time", () => {
  const row = firstCaller.rows[0];
  if (!row) throw new Error("Missing example");
  expect(
    smallestExample([
      { ...row, inputs: "long input" },
      { ...row, inputs: "x", unstable: true },
      { ...row, inputs: "<b>" },
    ])?.inputs,
  ).toBe("<b>");
});
it.each(["a", "b", "d", "merged"] as const)(
  "projects %s from current revision evidence",
  (name) => {
    const state = structuredClone(headerState(name));
    const id = name === "a" ? "change-3" : "change-1";
    expect(selectReviewBody(state, id).kind).toBe(name === "merged" ? "g" : name);
    const change = state.changes[id];
    if (!change?.proposal) throw new Error("Missing proposal");
    change.proposal = { ...change.proposal, revision: change.proposal.revision + 1 };
    if (name !== "merged") expect(selectReviewBody(state, id).kind).toBe("d");
  },
);
it("leaves unsupported evidence without a lead and never repeats raw summary claims", () => {
  const state = withMeasuredFacts({
    ...availableFacts,
    summary: ["Everything is safe"],
    measured_cone: [],
  });
  expect(selectReviewBody(state, "change-1").kind).toBe("other");
  expect(selectReviewBody(state, "change-1").summary).toEqual([]);
});
