import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

import type { ServerEvent, ChangeFacts, SuiteExecution } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { applyEvent, createInitialState } from "../state";
import type { State } from "../state";
import { detectorLine, discrepancies, ReviewFacts } from "./ReviewFacts";
import { headerState } from "./ReviewHeader.fixture";

const panelEvents = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const state = panelEvents
  .filter((event) => event.seq <= 20)
  .reduce(applyEvent, createInitialState());
function appendEvent(state: State, event: ServerEvent): State {
  return applyEvent(state, { ...event, seq: state.lastSeq + 1 });
}
const proposal = state.changes["change-1"]?.proposal;
if (!proposal) throw new Error("Missing proposal");
const facts: ChangeFacts = {
  summary: null,
  behavior_diff: null,
  measured_cone: null,
  review_cards: null,
  shape: null,
  shape_complete: null,
  change_id: proposal.change_id,
  revision: proposal.revision,
  base_commit: proposal.base_commit,
  head_commit: proposal.head_commit,
  main_commit: proposal.base_commit,
  candidate_tree: "tree",
  declared: { files: ["invented.py"], tests_run: true, tests: ["unverified"] },
  edited_paths: ["shop/pricing.py"],
  test_runs: [21],
  integrity: [],
  added_tests: [],
  run_findings: [],
  zone: null,
  reach: null,
  complete: false,
  problems: ["added_tests producer unavailable (package 4)", "detector unknown: timeout"],
};
const execution: SuiteExecution = {
  change_id: proposal.change_id,
  agent_id: proposal.agent_id,
  revision: proposal.revision,
  phase: "proposal",
  tested_commit: proposal.head_commit,
  tested_tree: "tree",
  suite: {
    outcome: "failed",
    passed: 1,
    failed: 1,
    skipped: 0,
    duration_ms: 12,
    failed_tests: ["tests/test_x.py::test_x"],
    cases_complete: false,
    cases: [{ test_id: "tests/test_x.py::test_x", outcome: "failed", detail: null }],
  },
};

describe("review evidence", () => {
  it.each(["b", "c"] as const)("preserves the entire non-panel markup for %s", (name) => {
    const view = headerState(name);
    const proposal = view.changes["change-1"]?.proposal;
    if (!proposal) throw new Error("Missing proposal");
    const html = renderToStaticMarkup(<ReviewFacts state={view} proposal={proposal} />);
    // Whole-output pins measured on the accepted 1ac8382 tip.
    expect(createHash("sha256").update(html).digest("hex")).toBe(
      name === "b"
        ? "4a1d2fccb8372fd478e338a99ba77a20ac8eb489dcc496939ca603025dbbf620"
        : "4b136f2685b3c0454e8cec31889d5fa96f5a43a1a5584d484f52ffcf55b6cb3e",
    );
  });
  const panelHtml = (record: ChangeFacts) =>
    renderToStaticMarkup(
      <ReviewFacts
        state={{
          ...state,
          testExecutions: { 21: execution },
          changeFacts: { [facts.change_id]: [record] },
        }}
        proposal={proposal}
        panel
      />,
    );
  it("labels the closed panel Details and omits the repeated size", () => {
    const html = panelHtml(facts);
    expect(html).toContain('aria-label="Details"');
    expect(html).toContain("<details>");
    expect(html).not.toMatch(/Review signals|Size:/);
  });
  it("keeps discrepancy text in an unlabeled list without the prefix", () => {
    const html = panelHtml(facts);
    expect(html).toContain("<ul><li>Said tests were run; no agent run_tests call recorded</li>");
    expect(html).not.toContain("Said versus did");
  });
  it("hides the empty detector line but retains unknown, parse errors and recorded changes", () => {
    expect(panelHtml({ ...facts, problems: [], integrity: [] })).not.toContain(
      "Tests in this change:",
    );
    expect(panelHtml(facts)).toContain("Test facts unknown: detector analysis incomplete");
    for (const [kind, line] of [
      ["parse_error", "Test facts unknown: parse error"],
      ["test_removed", "Tests in this change: 1 removed"],
    ] as const) {
      const integrity = [
        {
          kind,
          path: "tests/test_x.py",
          line: 1,
          text: "Check changed",
          node_id: null,
          evidence: [],
          test_id: null,
        },
      ];
      expect(panelHtml({ ...facts, problems: [], integrity })).toContain(line);
    }
  });
  it("labels added tests and retains test runs and incomplete evidence", () => {
    const html = panelHtml({
      ...facts,
      added_tests: [
        {
          test_id: "tests/test_x.py::test_x",
          before_seq: 20,
          after_seq: 21,
          result: "fails_before_passes_after",
        },
      ],
    });
    expect(html).toContain('aria-label="Added tests"');
    expect(html).toContain("fails before, passes after");
    expect(html).toContain("Tests #21 (proposal): failed; 1 passed, 1 failed, 0 skipped");
    expect(html).toContain(`Incomplete: ${facts.problems.join("; ")}`);
  });
  it.each(["b", "c"] as const)("excludes DESIGN vocabulary from panel Details for %s", (name) => {
    const view = headerState(name);
    const proposal = view.changes["change-1"]?.proposal;
    if (!proposal) throw new Error("Missing proposal");
    const html = renderToStaticMarkup(<ReviewFacts state={view} proposal={proposal} panel />);
    expect(html).not.toMatch(
      /invocations|unmatched|static cone bounds|signals|said versus did|probe|equivalence|\bAvailable\b/i,
    );
  });
  it("lists every added case with neutral before/after signals", () => {
    const kinds = [
      "fails_before_passes_after",
      "passes_before_and_after",
      "fails_after",
      "not_collected_after",
      "skipped",
      "unknown",
    ] as const;
    const view = {
      ...state,
      changeFacts: {
        [facts.change_id]: [
          {
            ...facts,
            added_tests: kinds.map((result, index) => ({
              test_id: `tests/test_added.py::test_${String(index)}`,
              before_seq: 21,
              after_seq: 22,
              result,
            })),
          },
        ],
      },
    };
    const html = renderToStaticMarkup(<ReviewFacts state={view} proposal={proposal} />);
    for (const label of [
      "fails before, passes after",
      "passes before and after",
      "fails after",
      "not collected after",
      "skipped",
      "unknown",
    ])
      expect(html).toContain(label);
    kinds.forEach((_, index) => {
      expect(html).toContain(`tests/test_added.py::test_${String(index)}`);
    });
    expect(html).toContain('aria-label="Added test signals"');
    expect(html).not.toMatch(/verdict|text-passed|text-failed|amber/);
  });
  it("keeps executions by seq and facts by revision through replay; a new run resets them", () => {
    const withSuite = appendEvent(state, {
      seq: 21,
      ts: "2026-10-27T12:00:00Z",
      type: "tests.executed",
      payload: execution,
    });
    const withFacts = appendEvent(withSuite, {
      seq: 22,
      ts: "2026-10-27T12:00:01Z",
      type: "change.facts_recorded",
      payload: facts,
    });
    expect(withFacts.testExecutions[21]).toEqual(execution);
    expect(withFacts.changeFacts[facts.change_id]).toEqual([facts]);
    expect(state.testExecutions).toEqual({});
    const started = panelEvents[0];
    if (started?.type !== "run.started") throw new Error("Missing start");
    const reset = applyEvent(withFacts, {
      ...started,
      payload: { ...started.payload, run_id: "run-new" },
    });
    expect(reset.testExecutions).toEqual(createInitialState().testExecutions);
    expect(reset.changeFacts).toEqual({});
  });

  it("shows claims against agent calls, failed ids, size and incomplete evidence without a verdict", () => {
    const view = {
      ...state,
      testExecutions: { 21: execution },
      changeFacts: { [facts.change_id]: [facts] },
    };
    const html = renderToStaticMarkup(<ReviewFacts state={view} proposal={proposal} />);
    for (const text of [
      "Said tests were run; no agent run_tests call recorded",
      "Named invented.py; no touch recorded",
      "Did not name shop/pricing.py; touch recorded",
      "failed ids: tests/test_x.py::test_x",
      "Size:",
      "Test facts unknown",
      "Incomplete:",
      "package 4",
      "Signals for your review. You decide.",
    ])
      expect(html).toContain(text);
    expect(html).not.toMatch(/safe|verdict|amber|#[0-9a-f]{6}/i);
  });

  it("shows the behavior-not-recorded message exactly once without facts or a behavior diff", () => {
    const view = { ...state, changeFacts: {} };
    const html = renderToStaticMarkup(<ReviewFacts state={view} proposal={proposal} />);

    expect(html.match(/Behavior evidence was not recorded for this revision\./g)).toHaveLength(1);
    expect(html).not.toContain('aria-label="Behavior diff"');
  });

  it("never presents stale revision facts as the current candidate's evidence", () => {
    const view = {
      ...state,
      changeFacts: { [facts.change_id]: [{ ...facts, revision: facts.revision + 1 }] },
    };
    const html = renderToStaticMarkup(<ReviewFacts state={view} proposal={proposal} />);
    expect(html).toContain("facts for this revision are unavailable");
    expect(html).toContain("Caller measurements not recorded for this revision");
  });

  it("preserves detector unknown, parse errors and neutral observations", () => {
    expect(detectorLine(facts)).toContain("unknown");
    const observation = {
      kind: "assertion_loosened",
      path: "tests/test_x.py",
      line: 1,
      text: "Check changed",
      node_id: null,
      evidence: [{ git_object: "tree", event_seq: null, path: null }],
      test_id: "tests/test_x.py::test_x",
    } as const;
    expect(
      detectorLine({
        ...facts,
        problems: [],
        integrity: [{ ...observation, evidence: [...observation.evidence] }],
      }),
    ).toBe("Tests in this change: 1 check changed");
    expect(
      detectorLine({
        ...facts,
        problems: [],
        integrity: [{ ...observation, evidence: [...observation.evidence], kind: "parse_error" }],
      }),
    ).toBe("Test facts unknown: parse error");
  });

  it("compares only explicit summary claims and leaves conflicting statements unknown", () => {
    const runs = [{ ...execution, phase: "agent" as const }];
    expect(discrepancies(facts, runs, "All tests passed.")).toContain(
      "Said tests pass; last agent run did not pass",
    );
    expect(discrepancies(facts, runs, "All tests passed. Tests failed.")).not.toContain(
      "Said tests pass; last agent run did not pass",
    );
    expect(discrepancies(facts, [execution], "I plan to run the tests.")).not.toContain(
      "Said tests pass; last agent run did not pass",
    );
  });

  it("does not treat truncated or missing case ids as proven non-execution", () => {
    expect(discrepancies(facts, [{ ...execution, phase: "agent" }])).toContain(
      "Named test unverified; execution evidence unavailable",
    );
    expect(
      discrepancies({ ...facts, declared: { tests_run: false, files: null, tests: null } }, [
        { ...execution, phase: "agent" },
      ]),
    ).toContain("Said tests were not run; agent run_tests calls recorded");
  });
});
