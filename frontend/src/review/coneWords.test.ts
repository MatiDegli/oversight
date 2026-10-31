import { expect, it } from "vitest";
import { selectConeWords } from "./coneWords";
import {
  availableFacts,
  firstCaller,
  measuredState,
  withMeasuredFacts,
} from "./measuredCallers.fixture";

it("suppresses every word without a proposal or any comparison, including d and d2", () => {
  expect(selectConeWords(measuredState, "missing")).toBeNull();
  const state = withMeasuredFacts();
  const change = state.changes["change-1"];
  if (!change) throw new Error("Missing change");
  expect(
    selectConeWords(
      { ...state, changes: { "change-1": { ...change, proposal: null } } },
      "change-1",
    ),
  ).toBeNull();
  expect(selectConeWords(measuredState, "change-1")).toBeNull();
  expect(
    selectConeWords(
      withMeasuredFacts({
        ...availableFacts,
        behavior_diff: [],
        measured_cone: [{ ...firstCaller, compared_count: 0 }],
      }),
      "change-1",
    ),
  ).toBeNull();
});

it.each([
  [{ compared_count: 0 }, "not measured"],
  [{ not_probed: "budget exhausted" }, "not measured"],
  [{ rows: [{ inputs: "x", before: "1", after: "2", unstable: true }] }, "varied"],
  [{ outcome: "changed", differing_count: 1, complete: false }, "differs"],
  [{ outcome: "unchanged", differing_count: 0, complete: true }, "same"],
  [{ outcome: "unchanged", differing_count: 0, complete: false }, null],
  [{ outcome: "not_compared", differing_count: 0 }, null],
  [{ outcome: "changed", differing_count: 0 }, null],
] as const)("projects evidence %j as %s", (patch, word) => {
  const state = withMeasuredFacts({
    ...availableFacts,
    behavior_diff: [
      { node_id: "seed", compared_count: 1, differing_count: 0, rows: [], not_probed: null },
    ],
    measured_cone: [
      { ...firstCaller, ...patch, rows: [...("rows" in patch ? patch.rows : firstCaller.rows)] },
    ],
  });
  const before = JSON.stringify(state);
  expect(selectConeWords(state, "change-1")?.get(firstCaller.node_id)).toBe(word);
  expect(JSON.stringify(state)).toBe(before);
});

it("uses behavior comparisons to show unmeasured callers, preserving one entry per static row", () => {
  const state = withMeasuredFacts({
    ...availableFacts,
    measured_cone: [],
    behavior_diff: [
      { node_id: "seed", compared_count: 1, differing_count: 0, rows: [], not_probed: null },
    ],
  });
  const words = selectConeWords(state, "change-1");
  expect(words?.size).toBe(state.changes["change-1"]?.proposal?.impact.entries.length);
  expect([...(words?.values() ?? [])].every((word) => word === "not measured")).toBe(true);
});
