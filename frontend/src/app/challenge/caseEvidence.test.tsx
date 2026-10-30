import type { ChangeFacts, CrossReview, ServerEvent } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { parseEvent } from "../../transport";
import { RecordedEvidence } from "./RecordedEvidence";
import { caseEvidence } from "./caseEvidence";
import { manifest, validateManifest } from "./manifest";
import { recording } from "./recording.fixture";
import { PhonePractice } from "./PhonePractice";
import { PracticePanel, freshAttempt } from "./PracticePanel";
const practice = {
  ...validateManifest(manifest, recording),
  revealThroughSeq: manifest.revealThroughSeq,
};
function required<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("Missing test evidence");
  return value;
}
const entry = required(practice.cases[0]);
const target = {
  change_id: entry.changeId,
  revision: entry.revision,
  head_commit: entry.head,
  base_commit: entry.proposal.base_commit,
  main_commit: entry.proposal.base_commit,
  candidate_tree: "tree",
};
const facts: ChangeFacts = {
  ...target,
  declared: null,
  edited_paths: ["shop/inventory.py"],
  test_runs: [],
  integrity: [],
  added_tests: [],
  run_findings: [],
  zone: null,
  reach: null,
  complete: false,
  problems: ["Probe incomplete"],
  summary: ["Recorded behavior summary"],
  behavior_diff: null,
  measured_cone: null,
  review_cards: null,
  shape: null,
  shape_complete: null,
};
const review: CrossReview = {
  ...target,
  author_model: "author",
  reviewer_model: "reviewer",
  reviewer_provider: "fake",
  reasoning: null,
  verdict: "changes_requested",
  findings: [{ severity: null, text: "Check the boundary", path: null, line: null, node_id: null }],
  complete: true,
  problems: [],
};
const additions: ServerEvent[] = [
  { seq: 0, ts: required(practice.events[0]).ts, type: "change.facts_recorded", payload: facts },
  { seq: 0, ts: required(practice.events[0]).ts, type: "change.reviewed", payload: review },
  {
    seq: 0,
    ts: required(practice.events[0]).ts,
    type: "tests.executed",
    payload: {
      change_id: entry.changeId,
      agent_id: entry.proposal.agent_id,
      revision: entry.revision,
      phase: "proposal",
      tested_commit: entry.head,
      tested_tree: "tree",
      suite: entry.proposal.tests,
    },
  },
];
function extended(extra = additions) {
  const events = [...practice.events.slice(0, 63), ...extra, ...practice.events.slice(63)].map(
    (event, i) => parseEvent(JSON.stringify({ ...event, seq: i + 1 })),
  );
  const candidate = structuredClone(manifest);
  candidate.cutSeq += extra.length;
  candidate.revealThroughSeq += extra.length;
  candidate.recording.event_count += extra.length;
  candidate.cases.forEach((item) => {
    item.resolutionSeq += extra.length;
  });
  return {
    ...validateManifest(candidate, {
      text: events.map((event) => JSON.stringify(event)).join("\n"),
      provenance: candidate.recording,
    }),
    revealThroughSeq: candidate.revealThroughSeq,
  };
}
it.each([
  [0, [7, 10], [259, 236], 29, "60091ff"],
  [1, [7], [239], 32, "15087d4"],
] as const)(
  "selects recorded runs, absences and history for case %i",
  (index, steps, durations, passed, commit) => {
    const item = required(practice.cases[index]);
    const evidence = caseEvidence(practice, item);
    expect(evidence.tests.status).toBe("present");
    if (evidence.tests.status !== "present") throw new Error("Missing tests");
    const runs = evidence.tests.value.filter((run) => run.source === "step");
    expect(runs.map((run) => run.step)).toEqual(steps);
    expect(runs.map((run) => run.tests.duration_ms)).toEqual(durations);
    expect(
      runs.every(
        (run) => run.tests.passed === passed && run.tests.failed === 0 && run.tests.skipped === 0,
      ),
    ).toBe(true);
    expect(evidence.tests.value.at(-1)?.source).toBe("proposal");
    for (const part of [evidence.executions, evidence.facts, evidence.reviews])
      expect(part.status).toBe("absent");
    expect(evidence.history.status).toBe("present");
    if (evidence.history.status !== "present") throw new Error("Missing history");
    expect(evidence.history.value.map((event) => event.type)).toEqual(["proposal", "resolution"]);
    const resolution = required(evidence.history.value[1]);
    expect(resolution.value.revision).toBe(1);
    if (resolution.type !== "resolution") throw new Error("Missing resolution");
    expect(resolution.value).toMatchObject({
      decision: "approved",
      merge: "merged",
      decided_by: null,
      comment: null,
    });
    expect(resolution.value.merge_commit?.startsWith(commit)).toBe(true);
  },
);
it("shows later recorded evidence and ignores stale revisions, wrong heads and post-cut evidence", () => {
  const populated = extended();
  const evidence = caseEvidence(populated, required(populated.cases[0]));
  for (const part of [evidence.executions, evidence.facts, evidence.reviews])
    expect(part.status).toBe("present");
  expect(caseEvidence(populated, required(populated.cases[1])).facts.status).toBe("absent");
  for (const mismatch of ["revision", "head"] as const) {
    const stale = additions.map((event) => {
      const copy = structuredClone(event);
      if (mismatch === "revision" && "revision" in copy.payload) copy.payload.revision = 2;
      if (mismatch === "head") {
        if ("head_commit" in copy.payload) copy.payload.head_commit = "other";
        if ("tested_commit" in copy.payload) copy.payload.tested_commit = "other";
      }
      return copy;
    });
    const changed = extended(stale);
    const selected = caseEvidence(changed, required(changed.cases[0]));
    for (const part of [selected.executions, selected.facts, selected.reviews])
      expect(part.status).toBe("absent");
  }
  populated.cut.lastSeq = 63;
  const afterCut = caseEvidence(populated, required(populated.cases[0]));
  for (const part of [afterCut.executions, afterCut.facts, afterCut.reviews])
    expect(part.status).toBe("absent");
});
it("bounds step runs by the previous proposal or resolution, retaining request-changes history", () => {
  const changed = structuredClone(practice);
  changed.events.splice(
    0,
    0,
    {
      seq: 40,
      ts: required(practice.events[0]).ts,
      type: "change.proposed",
      payload: { ...entry.proposal, revision: 0 },
    },
    {
      seq: 41,
      ts: required(practice.events[0]).ts,
      type: "change.resolved",
      payload: {
        ...entry.resolution,
        revision: 0,
        decision: "changes_requested",
        merge: null,
        merge_commit: null,
        decided_by: "person",
        comment: "Please revise",
      },
    },
  );
  const selected = caseEvidence(changed, entry);
  if (selected.tests.status !== "present" || selected.history.status !== "present")
    throw new Error("Missing evidence");
  expect(
    selected.tests.value.filter((run) => run.source === "step").map((run) => run.step),
  ).toEqual([10]);
  expect(
    selected.history.value.some(
      (event) => event.type === "resolution" && event.value.comment === "Please revise",
    ),
  ).toBe(true);
});
it("keeps history through the reveal bound, including rules, conflicts and comments", () => {
  const changed = structuredClone(practice);
  const event: ServerEvent = {
    seq: 70,
    ts: required(practice.events[0]).ts,
    type: "change.resolved",
    payload: {
      ...entry.resolution,
      decided_by: "rules",
      merge: "conflict",
      merge_commit: null,
      comment: "Combined tests failed",
      conflict: { kind: "tests", paths: [], tests: entry.proposal.tests },
    },
  };
  changed.events.push(event);
  expect(caseEvidence(changed, entry).history).toEqual(caseEvidence(practice, entry).history);
  changed.revealThroughSeq = 70;
  const history = caseEvidence(changed, entry).history;
  expect(history.status === "present" && history.value.at(-1)?.value).toEqual(event.payload);
});
const handlers = {
  onStart: vi.fn(),
  onAnswer: vi.fn(),
  onNext: vi.fn(),
  onReveal: vi.fn(),
  onRestart: vi.fn(),
  onExit: vi.fn(),
};
it.each([PhonePractice, PracticePanel])(
  "renders four evidence parts, absence limits and future facts in %s",
  (Panel) => {
    for (const source of [practice, extended()]) {
      const html = renderToStaticMarkup(
        <Panel
          {...handlers}
          practice={source}
          attempt={{ ...freshAttempt(), started: true, revealed: true }}
        />,
      );
      for (const label of ["Agent’s test runs", /review facts/i, /cross-review/i, "Review history"])
        expect(html.split(label).length - 1).toBeGreaterThanOrEqual(2);
      expect(html).toContain(
        "Missing evidence does not establish a clean review, behavior or safety.",
      );
      expect(html).toContain("decision maker not recorded");
      expect(html).not.toContain("<details open");
      if (source !== practice) {
        expect(html).toContain("Recorded behavior summary");
        expect(html).toContain("Check the boundary");
        expect(html).toContain("Probe incomplete");
      }
    }
  },
);

function renderEvidence(source = practice) {
  return renderToStaticMarkup(
    <RecordedEvidence practice={source} entry={required(source.cases[0])} />,
  );
}
it("renders execution phases with spaces and preserves the verdict wording", () => {
  const source = extended(
    additions.map((event) =>
      event.type === "tests.executed"
        ? { ...event, payload: { ...event.payload, phase: "base_probe" } }
        : event,
    ),
  );
  const html = renderEvidence(source);
  expect(html).toContain("Execution (base probe)");
  expect(html).not.toContain("base_probe");
  expect(html).toContain("changes requested");
});
it("renders review severities in sentence case without changing finding text", () => {
  const text = "Check BLOCKING and base_probe exactly as recorded";
  const source = extended(
    additions.map((event) =>
      event.type === "change.reviewed"
        ? {
            ...event,
            payload: {
              ...event.payload,
              findings: (["BLOCKING", "SHOULD-FIX", "NIT"] as const).map((severity) => ({
                severity,
                text,
                path: null,
                line: null,
                node_id: null,
              })),
            },
          }
        : event,
    ),
  );
  const html = renderEvidence(source);
  for (const severity of ["Blocking", "Should-fix", "Nit"])
    expect(html).toContain(`${severity}: ${text}`);
});
it("shows the tested-head caveat only when agent steps are recorded", () => {
  const caveat =
    "Agent steps belong to this proposal round; their exact tested heads and test execution records were not recorded.";
  expect(renderEvidence()).toContain(caveat);
  const source = extended();
  source.events = source.events.filter(
    (event) => event.type !== "agent.step" || event.payload.tool !== "run_tests",
  );
  expect(renderEvidence(source)).toContain("Execution (proposal)");
  expect(renderEvidence(source)).not.toContain(caveat);
});
it("labels the final test result Proposal", () => {
  const html = renderEvidence();
  expect(html).toContain(">Proposal: passed");
  expect(html).not.toContain("Proposal result");
});
it.each([false, true])("includes executions in the test-run preview (with steps: %s)", (steps) => {
  const execution = required(additions.find((event) => event.type === "tests.executed"));
  const source = extended([
    {
      ...execution,
      payload: { ...execution.payload, suite: { ...execution.payload.suite, passed: 32 } },
    },
  ]);
  if (!steps)
    source.events = source.events.filter(
      (event) => event.type !== "agent.step" || event.payload.tool !== "run_tests",
    );
  const html = renderEvidence(source);
  expect(html).toContain(
    `Agent’s test runs</span> <span class="evidence-preview">${steps ? "3 runs" : "1 run"}, last 32 of 32 passed</span>`,
  );
  expect(html).not.toContain("Not recorded for this revision: agent’s test runs");
});
it("preserves earlier facts and their incompleteness after a later complete record", () => {
  const first = required(additions.find((event) => event.type === "change.facts_recorded"));
  const latest = { ...facts, complete: true, problems: [], summary: ["Later complete record"] };
  const source = extended([first, { ...first, payload: latest }]);
  expect(caseEvidence(source, required(source.cases[0])).facts).toEqual({
    status: "present",
    value: [facts, latest],
  });
  const html = renderEvidence(source);
  for (const text of ["Recorded behavior summary", "Probe incomplete", "Later complete record"])
    expect(html).toContain(text);
  expect(html).toContain(
    'Review facts</span> <span class="evidence-preview">Recorded, incomplete</span>',
  );
});

it("names three absent kinds in one line and gives them no rows", () => {
  const source = structuredClone(practice);
  source.events = source.events.filter(
    (event) => event.type !== "change.proposed" && event.type !== "change.resolved",
  );
  const html = renderEvidence(source);
  expect(html.match(/<summary>/g)).toHaveLength(1);
  expect(html).toContain(
    "Not recorded for this revision: review facts, cross-review and review history.",
  );
});
it("omits the kind-absence line when every kind is recorded", () => {
  const html = renderEvidence(extended());
  expect(html.match(/<summary>/g)).toHaveLength(4);
  expect(html).toContain("<span>Review facts</span>");
  expect(html).not.toContain("Not recorded for this revision:");
});
it("leaves only the heading and one absence sentence when all kinds are absent", () => {
  const source = structuredClone(practice);
  source.events = [];
  const html = renderEvidence(source);
  expect(html).not.toContain("<summary>");
  expect(html).not.toContain('class="evidence-rows"');
  expect(html).toContain(
    "Not recorded for this revision: agent’s test runs, review facts, cross-review and review history.",
  );
});
