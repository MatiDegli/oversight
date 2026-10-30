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

it.each(["missing", "equal", "other function", "partial", "rendered"] as const)(
  "only promises examples when each choice has a rendered example: %s",
  (shape) => {
    const state = structuredClone(headerState("a"));
    const facts = state.changeFacts["change-3"]?.at(-1);
    const cards = facts?.review_cards;
    if (!facts || !cards?.length || !cards[0]?.example) throw new Error("Missing choice examples");
    for (const [index, card] of cards.entries()) {
      if (shape === "missing" || (shape === "partial" && index === 0)) card.example = null;
      if (shape === "equal" && card.example) card.example.after = card.example.before;
      if (shape === "other function") card.node_id = "another:function";
    }
    const view = selectReviewBody(state, "change-3");
    expect(view.kind).toBe("a");
    expect(view.summary[0]).toContain("inputs compared");
    expect(view.summary[0]?.includes("one example of each kind is shown above")).toBe(
      shape === "rendered",
    );
  },
);
