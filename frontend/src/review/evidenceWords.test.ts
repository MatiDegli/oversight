import type { RuleAssessment, RuleId } from "@contracts";
import { expect, it } from "vitest";
import { required } from "./behavior.fixture";
import { firstCaller } from "./measuredCallers.fixture";
import { headerState } from "./ReviewHeader.fixture";
import { evidenceWords, mergedRuleWords, recordingBudgetReached, ruleWords } from "./evidenceWords";

it.each([
  ["unsupported node kind", "this kind of node is not measured yet"],
  ["version unavailable", "the base or proposed version is unavailable"],
  ["default value not literal", "a default value is not a plain literal"],
  ["no literal value for a required argument", "a required argument has no plain literal value"],
  ["no literal receiver", "no plain object was found to call it on"],
  ["signature not supported", "its signature is not supported yet"],
  ["async or generator", "async functions and generators are not measured yet"],
  ["side effect: global or nonlocal assignment", "it assigns a global or nonlocal name"],
  ["side effect: environment access", "it reads the environment"],
  ["side effect: randomness", "it uses randomness"],
  ["side effect: time access", "it reads the clock"],
  ["side effect: network access", "it uses the network"],
  ["side effect: print or input", "it prints or reads input"],
  ["side effect: file access", "it reads or writes files"],
  ["side effect: property getter effects", "a property getter has side effects"],
  ["input not representable", "its inputs could not be recorded"],
  ["repeat incomplete", "the repeated run did not finish"],
  ["probe failed", "the measurement run failed"],
  ["recording budget reached", "recording budget reached"],
  ["probe unavailable", "no reason was recorded"],
  ["side effect: randomness; side effect: time access", "it uses randomness; it reads the clock"],
  ["unknown; version unavailable", "the base or proposed version is unavailable"],
  ["unknown; toString; __proto__", null],
  [null, null],
])("maps only published words: %s", (reason, expected) => {
  expect(evidenceWords(reason)).toBe(expected);
});
it.each(["method", "class"])("names unsupported %s nodes", (kind) => {
  expect(evidenceWords("unsupported node kind", kind)).toBe(
    "methods and classes are not measured yet",
  );
});
it("recognizes both published budget signals, including a joined node reason", () => {
  const node = { node_id: "f", compared_count: 0, differing_count: 0, rows: [], not_probed: null };
  expect(recordingBudgetReached(node)).toBe(false);
  expect(
    recordingBudgetReached({ ...node, not_probed: "probe failed; recording budget reached" }),
  ).toBe(true);
  expect(recordingBudgetReached({ ...node, not_probed: "budget exhausted" })).toBe(false);
  expect(
    recordingBudgetReached({
      ...firstCaller,
      not_probed: null,
      not_compared: ["caller measurement budget exhausted"],
    }),
  ).toBe(true);
  expect(
    recordingBudgetReached({ ...firstCaller, not_probed: null, not_compared: ["unknown"] }),
  ).toBe(false);
});

const resolution = () =>
  structuredClone(required(required(headerState("merged").changes["change-1"]).resolution));
const assessed = (rule: RuleId, result: RuleAssessment["result"] = "pass"): RuleAssessment => ({
  rule,
  result,
  observed: null,
  threshold: {},
  evidence: [],
});
it.each([
  [[], null],
  [["combined_tests"], "Tests pass."],
  [
    ["small_change", "combined_tests"],
    "Tests pass and the change is small (at most 3 files and 60 lines).",
  ],
  [
    ["within_zone", "combined_tests", "tests_reach_changes"],
    "Tests pass, every changed function is reached by a test, and the change stays inside its zone.",
  ],
  [
    ["within_zone", "combined_tests", "tests_reach_changes", "cross_review"],
    "Tests pass, every changed function is reached by a test, and the change stays inside its zone. 1 more rule passed.",
  ],
] satisfies [RuleId[], string | null][])("formats passed rules %s", (ids, line) => {
  const resolved = resolution();
  required(resolved.assessment).rules = [
    ...ids.map((id) => assessed(id)),
    assessed("test_integrity", "fail"),
    assessed("known_spend", "unknown"),
  ];
  expect(mergedRuleWords(resolved).line).toBe(line);
  expect(mergedRuleWords(resolved).details).toEqual(
    (Object.keys(ruleWords) as RuleId[])
      .filter((id) => new Set<RuleId>(ids).has(id))
      .map((id) => `${ruleWords[id][1]}: passed`),
  );
});
it("names the eight recorded safe-merge-probes-v1 passes in approved order", () => {
  const resolved = resolution();
  expect(resolved.assessment?.policy_version).toBe("safe-merge-probes-v1");
  expect(mergedRuleWords(resolved)).toEqual({
    line: "Tests pass, a second model's review found nothing blocking, and the change is small (at most 3 files and 60 lines). 5 more rules passed.",
    details: [
      "Tests on the combined result: passed",
      "Second-model review: passed",
      "Change size: passed",
      "Possibly affected functions: passed",
      "Existing tests untouched: passed",
      "Added tests fail before the change: passed",
      "Test integrity: passed",
      "Known spend: passed",
    ],
  });
});
it("distinguishes recorded grant coverage from passes and counts rules once", () => {
  const resolved = required(required(headerState("mergedGrant").changes["change-1"]).resolution);
  const words = mergedRuleWords(resolved);
  expect(words.line).toBe(
    "Tests pass, the change is small (at most 3 files and 60 lines), and existing tests were not edited or deleted. 1 more rule passed. Your grant covered 1 rule that could not be checked.",
  );
  expect(words.details).toEqual([
    "Tests on the combined result: passed",
    "Change size: passed",
    "Possibly affected functions: covered by your grant",
    "Existing tests untouched: passed",
    "Known spend: passed",
  ]);
});
it("omits rule copy without an assessment, a rules merge or any passed rule", () => {
  expect(mergedRuleWords(null).line).toBeNull();
  const resolved = resolution();
  expect(mergedRuleWords({ ...resolved, assessment: null }).line).toBeNull();
  expect(mergedRuleWords({ ...resolved, decided_by: "person" })).toEqual({
    line: null,
    details: [],
  });
  required(resolved.assessment).rules = [assessed("combined_tests", "unknown")];
  expect(mergedRuleWords(resolved).line).toBeNull();
});
