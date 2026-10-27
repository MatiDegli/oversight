import { readFileSync } from "node:fs";

import type { ServerEvent, ChangeFacts, SuiteExecution } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { applyEvent, createInitialState } from "../state";
import type { State } from "../state";
import { detectorLine, discrepancies, ReviewFacts } from "./ReviewFacts";

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

  it("never presents stale revision facts as the current candidate's evidence", () => {
    const view = {
      ...state,
      changeFacts: { [facts.change_id]: [{ ...facts, revision: facts.revision + 1 }] },
    };
    expect(renderToStaticMarkup(<ReviewFacts state={view} proposal={proposal} />)).toContain(
      "facts for this revision are unavailable",
    );
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
