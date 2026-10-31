import { expect, it } from "vitest";
import { headerState } from "./ReviewHeader.fixture";
import { availableFacts, firstCaller, withMeasuredFacts } from "./measuredCallers.fixture";
import {
  nothingComparedLine,
  partialLines,
  selectCallerComparison,
  selectReviewBody,
  smallestExample,
} from "./reviewBodyEvidence";

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
it.each(["a", "b", "c", "d", "d2", "merged"] as const)(
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
it("keeps complete stable evidence without a differing caller title", () => {
  const state = withMeasuredFacts({
    ...availableFacts,
    summary: ["Everything is safe"],
    complete: true,
    measured_cone: [],
    behavior_diff: [
      {
        node_id: "shop.pricing:compute_price",
        compared_count: 2,
        differing_count: 0,
        rows: [],
        not_probed: null,
      },
    ],
  });
  const { kind, summary } = selectReviewBody(state, "change-1");
  expect(kind).toBe("other");
  expect(summary[0]).toContain("2 inputs compared, 0 differ.");
  expect(summary.join(" ")).not.toContain("Everything is safe");
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

it.each([
  ["incomplete", "c"],
  ["empty", "d2"],
  ["not_probed", "c"],
  ["unstable", "c"],
  ["incomplete caller", "c"],
  ["b incomplete", "c"],
] as const)("selects %s evidence as %s", (shape, kind) => {
  const state = structuredClone(headerState("b"));
  const facts = state.changeFacts["change-1"]?.at(-1);
  if (!facts?.measured_cone?.[0]) throw new Error("Missing facts");
  const caller = facts.measured_cone[0];
  if (shape === "incomplete" || shape === "b incomplete") facts.complete = false;
  if (shape === "empty") {
    facts.measured_cone = [];
    facts.behavior_diff = [];
  }
  if (shape === "not_probed") caller.not_probed = "unknown reason";
  if (shape === "unstable") caller.rows = caller.rows.map((r) => ({ ...r, unstable: true }));
  if (shape === "incomplete caller") caller.complete = false;
  expect(selectReviewBody(state, "change-1").kind).toBe(kind);
});
it("uses c wording and only the stable caller supplies an example", () => {
  const state = headerState("c");
  const lines = partialLines(state, "change-1");
  expect(lines.map((l) => l.line)).toEqual([
    "Cart.quote_add: 3 of 12 matched calls changed; 3 calls could not be matched.",
    "Cart.subtotals: results varied between runs; no difference established.",
    "Customer.quote: not measured, recording budget reached.",
    "compute_price: 2 inputs compared; results varied between runs.",
  ]);
  expect(lines.filter((l) => l.example)).toHaveLength(1);
  expect(selectCallerComparison(state, "change-1").differing).toHaveLength(1);
  expect(selectCallerComparison(state, "change-1").same).toEqual([]);
  expect(selectReviewBody(state, "change-1").summary[0]).toBe(
    "2 of 9 callers measured; 7 were not.",
  );
  const caller = state.changeFacts["change-1"]?.at(-1)?.measured_cone?.[2];
  if (!caller) throw new Error("Missing caller");
  for (const reason of ["Caller recording budget reached", "unknown", null]) {
    caller.not_probed = reason;
    expect(partialLines(state, "change-1")[2]?.line).toBe("Customer.quote: not measured.");
  }
});
it("ends a stable differing caller's partial line with a period and keeps its example", () => {
  const state = headerState("c");
  const caller = state.changeFacts["change-1"]?.at(-1)?.measured_cone?.[0];
  if (!caller) throw new Error("Missing caller");
  caller.unmatched_before = 0;
  caller.unmatched_after = 0;
  expect(selectReviewBody(state, "change-1").kind).toBe("c");
  const line = partialLines(state, "change-1")[0];
  expect(line?.line).toBe("Cart.quote_add: 3 of 12 recorded calls changed.");
  expect(line?.example).toEqual(smallestExample(caller.rows));
});
it("omits the callers measured line for partial evidence with no exact callers", () => {
  const state = headerState("c");
  const proposal = state.changes["change-1"]?.proposal;
  if (!proposal) throw new Error("Missing proposal");
  proposal.impact = { ...proposal.impact, entries: [], omitted_by_depth: 0, omitted_by_limit: 0 };
  const { kind, summary } = selectReviewBody(state, "change-1");
  expect(kind).toBe("c");
  expect(summary).toContain("No exact callers.");
  expect(summary.join(" ")).not.toContain("callers measured");
  expect(summary).toContain("3 of 36 functions compared.");
});
it.each([
  [null, "Nothing was compared."],
  [
    "compute_price could not run outside its test setup",
    "Nothing was compared: compute_price could not run outside its test setup.",
  ],
  ["Server Reason.", "Nothing was compared: Server Reason."],
  ["unsupported node kind", "Nothing was compared: this node type is not probed yet."],
  ["version unavailable", "Nothing was compared: the base or proposed version is unavailable."],
  [
    "probe unavailable",
    "Nothing was compared: behavior probing was unavailable; no further reason was published.",
  ],
])("formats d2 reason %s", (reason, expected) => {
  const state = headerState("d2");
  const probe = state.changeFacts["change-1"]?.at(-1)?.behavior_diff?.[0];
  if (!probe) throw new Error("Missing probe");
  probe.not_probed = reason;
  expect(nothingComparedLine(state, "change-1")).toBe(expected);
  if (reason === "unsupported node kind") {
    const node = state.graph?.nodes.find((n) => n.id === probe.node_id);
    if (!node) throw new Error("Missing node");
    node.kind = "method";
    expect(nothingComparedLine(state, "change-1")).toBe(
      "Nothing was compared: methods and classes are not probed yet.",
    );
  }
});
