// Hand-written evidence layered on the built review shell; never a recorded or model run.
import type {
  ChangeFacts,
  EvidenceRef,
  RunRuleFinding,
  ShapeFact,
  SuiteExecution,
} from "@contracts";
import { parameterState } from "./NewParameterCalls.fixture";

export const runKinds = ["loosened", "removed", "skipped", "deleted"] as const;
export const shapeKinds = [
  "function_length",
  "import_added",
  "import_cycle",
  "duplicate_logic",
] as const;
export const shapeStates = [
  "not-recorded",
  "empty-complete",
  "empty-incomplete",
  "populated-complete",
  "populated-incomplete",
  "completeness-unavailable",
] as const;
export type FindingState =
  | (typeof runKinds)[number]
  | (typeof shapeKinds)[number]
  | (typeof shapeStates)[number]
  | "all"
  | "baseline";
export const references: EvidenceRef[] = [
  { event_seq: 12, git_object: null, path: null },
  {
    event_seq: null,
    git_object: "0123456789abcdef0123456789abcdef01234567",
    path: "shop/pricing.py",
  },
  {
    event_seq: 12,
    git_object: "abcdef0123456789abcdef0123456789abcdef0123",
    path: "tests/test_pricing.py",
  },
];
export const shapes: ShapeFact[] = shapeKinds.map((kind, index) => ({
  kind,
  path: "shop/pricing.py",
  node_id: index % 2 ? null : "shop.pricing:compute_price",
  message:
    [
      "Function grew from 24 to 86 lines.",
      "Import added: shop.discounts.",
      "Import cycle observed: shop.pricing, shop.discounts.",
      "Matching statement structure retained in two functions; names and literals may differ.",
    ][index] ?? "",
  evidence: references,
}));
export function findingFacts(state: ReturnType<typeof parameterState>): ChangeFacts {
  const facts = state.changeFacts["change-1"]?.at(-1);
  if (!facts) throw new Error("Missing hand-written facts");
  return facts;
}
export function findingState(name: FindingState = "all", comparison = true) {
  const state = parameterState("mixed");
  const facts = findingFacts(state);
  if (!comparison) {
    facts.behavior_diff = null;
    facts.measured_cone = null;
    facts.review_cards = null;
  }
  facts.run_findings = [];
  facts.shape = null;
  facts.shape_complete = null;
  if (name === "baseline") return state;
  const kinds: readonly RunRuleFinding["kind"][] =
    name === "all" ? runKinds : runKinds.filter((kind) => kind === name);
  facts.integrity = kinds.map((kind, index) => ({
    kind:
      kind === "loosened"
        ? "assertion_loosened"
        : kind === "skipped"
          ? "skip_added"
          : "test_removed",
    test_id: `tests/test_pricing.py::test_${kind}`,
    path: "tests/test_pricing.py",
    line: 17 + index,
    text: `Observed ${kind} test check.`,
    evidence: references,
    node_id: null,
  }));
  facts.run_findings = kinds.map((kind, index) => ({
    kind,
    test_id: `tests/test_pricing.py::test_${kind}`,
    failed_seq: 10,
    edited_seq: 12,
    passed_seq: 14,
    fact_index: index,
  }));
  const execution: SuiteExecution = {
    change_id: "change-1",
    agent_id: "agent-1",
    revision: 0,
    tested_commit: "historical-head",
    tested_tree: "historical-tree",
    phase: "agent",
    suite: {
      outcome: "failed",
      passed: 2,
      failed: 1,
      skipped: 0,
      duration_ms: 20,
      failed_tests: kinds.map((kind) => `tests/test_pricing.py::test_${kind}`),
      cases: null,
      cases_complete: true,
    },
  };
  state.testExecutions[10] = execution;
  state.testExecutions[14] = {
    ...execution,
    suite: { ...execution.suite, outcome: "passed", failed: 0, failed_tests: [] },
  };
  if (shapeKinds.some((kind) => kind === name)) {
    facts.shape = structuredClone(shapes.filter((shape) => shape.kind === name));
    facts.shape_complete = true;
  } else if (name === "all" || shapeStates.some((kind) => kind === name)) {
    facts.shape =
      name === "not-recorded" ? null : name.startsWith("empty") ? [] : structuredClone(shapes);
    facts.shape_complete =
      name === "not-recorded" || name === "completeness-unavailable"
        ? null
        : name === "empty-complete" || name === "populated-complete";
  }
  return state;
}
