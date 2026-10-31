import type { ChangeResolved, NodeBehaviorDiff, MeasuredCaller, RuleId } from "@contracts";

const reasons: Record<string, string> = {
  "unsupported node kind": "this kind of node is not measured yet",
  "version unavailable": "the base or proposed version is unavailable",
  "default value not literal": "a default value is not a plain literal",
  "no literal value for a required argument": "a required argument has no plain literal value",
  "no literal receiver": "no plain object was found to call it on",
  "signature not supported": "its signature is not supported yet",
  "async or generator": "async functions and generators are not measured yet",
  "side effect: global or nonlocal assignment": "it assigns a global or nonlocal name",
  "side effect: environment access": "it reads the environment",
  "side effect: randomness": "it uses randomness",
  "side effect: time access": "it reads the clock",
  "side effect: network access": "it uses the network",
  "side effect: print or input": "it prints or reads input",
  "side effect: file access": "it reads or writes files",
  "side effect: property getter effects": "a property getter has side effects",
  "input not representable": "its inputs could not be recorded",
  "repeat incomplete": "the repeated run did not finish",
  "probe failed": "the measurement run failed",
  "recording budget reached": "recording budget reached",
  "probe unavailable": "no reason was recorded",
};

const parts = (reason: string | null) => reason?.split("; ") ?? [];

/** Unknown public phrases never become panel copy. */
export function evidenceWords(reason: string | null, kind?: string): string | null {
  const words = parts(reason).flatMap((part) => {
    if (part === "unsupported node kind" && (kind === "method" || kind === "class"))
      return ["methods and classes are not measured yet"];
    return Object.hasOwn(reasons, part) ? [reasons[part]] : [];
  });
  return words.length ? words.join("; ") : null;
}

export function recordingBudgetReached(evidence: NodeBehaviorDiff | MeasuredCaller): boolean {
  return (
    parts(evidence.not_probed).includes("recording budget reached") ||
    ("not_compared" in evidence &&
      evidence.not_compared.includes("caller measurement budget exhausted"))
  );
}

// Object insertion order is the approved reading order; Record makes RuleId additions fail tsc.
export const ruleWords = {
  combined_tests: ["tests pass", "Tests on the combined result"],
  tests_reach_changes: ["every changed function is reached by a test", "Tests reach the changes"],
  within_zone: ["the change stays inside its zone", "Inside the zone"],
  cross_review: ["a second model's review found nothing blocking", "Second-model review"],
  small_change: ["the change is small (at most 3 files and 60 lines)", "Change size"],
  small_cone: ["at most 5 functions are possibly affected", "Possibly affected functions"],
  existing_tests_untouched: [
    "existing tests were not edited or deleted",
    "Existing tests untouched",
  ],
  added_tests_fail_before: [
    "every added test fails before the change and passes after",
    "Added tests fail before the change",
  ],
  test_integrity: ["no test was removed, skipped or loosened", "Test integrity"],
  run_test_integrity: ["the test runs passed their integrity checks", "Test run integrity"],
  protected_paths: ["no protected path was edited", "Protected paths"],
  known_spend: ["spending is known and under half the budget", "Known spend"],
} satisfies Record<RuleId, readonly [string, string]>;

/** Use the resolution's recorded results and grant uses, never the current policy. */
export function mergedRuleWords(resolution: ChangeResolved | null | undefined) {
  const assessment = resolution?.assessment;
  if (resolution?.decided_by !== "rules" || resolution.merge !== "merged" || !assessment)
    return { line: null, details: [] };
  const ordered = (Object.keys(ruleWords) as RuleId[]).flatMap((id) => {
    const rule = assessment.rules.find((r) => r.rule === id);
    const covered = rule?.result === "unknown" && resolution.grants?.some((use) => use.rule === id);
    return rule?.result === "pass" || covered ? [{ id, passed: rule.result === "pass" }] : [];
  });
  const passed = ordered.filter((r) => r.passed);
  const clauses = passed.slice(0, 3).map((r) => ruleWords[r.id][0]);
  let line =
    clauses.length === 3
      ? `${clauses.slice(0, 2).join(", ")}, and ${clauses.slice(2).join("")}`
      : clauses.join(" and ");
  if (line) {
    line = `${line.charAt(0).toUpperCase()}${line.slice(1)}.`;
    const more = passed.length - 3;
    if (more > 0) line += ` ${String(more)} more rule${more === 1 ? "" : "s"} passed.`;
    const covered = ordered.length - passed.length;
    if (covered)
      line += ` Your grant covered ${String(covered)} rule${covered === 1 ? "" : "s"} that could not be checked.`;
  }
  return {
    line: line || null,
    details: ordered.map(
      (r) => `${ruleWords[r.id][1]}: ${r.passed ? "passed" : "covered by your grant"}`,
    ),
  };
}
