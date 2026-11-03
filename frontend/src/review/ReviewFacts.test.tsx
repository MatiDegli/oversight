import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

import type { ServerEvent, ChangeFacts, SuiteExecution } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { applyEvent, createInitialState } from "../state";
import type { State } from "../state";
import { detectorLine, discrepancies, ReviewFacts } from "./ReviewFacts";
import { headerState } from "./ReviewHeader.fixture";
import { ReviewBody } from "./ReviewBody";
import { measuredFacts } from "./measuredCallers.fixture";

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
  parameter_extensions: null,
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
  it.each(["b", "c"] as const)(
    "preserves non-panel evidence alongside structural availability for %s",
    (name) => {
      const view = headerState(name);
      // Freeze the accepted input as well as its non-panel output; c now uses public reasons.
      if (name === "c") {
        const current = view.changeFacts["change-1"]?.at(-1);
        if (!current) throw new Error("Missing facts");
        current.measured_cone = structuredClone(measuredFacts[1]?.measured_cone ?? []);
        const caller = current.measured_cone[2];
        if (!caller) throw new Error("Missing caller");
        caller.not_probed = "budget exhausted";
        current.behavior_diff = [
          {
            node_id: "shop.pricing:compute_price",
            compared_count: 2,
            differing_count: 0,
            rows: [
              { inputs: "unit_price=int:100", before: "int:500", after: "int:700", unstable: true },
            ],
            not_probed: null,
          },
        ];
      }
      const proposal = view.changes["change-1"]?.proposal;
      if (!proposal) throw new Error("Missing proposal");
      const html = renderToStaticMarkup(<ReviewFacts state={view} proposal={proposal} />);
      // Whole-output pins include the explicit not-recorded structural state.
      expect(createHash("sha256").update(html).digest("hex"), html).toBe(
        name === "b"
          ? "91a9640747745902514798e79df4d27de268245b8fd3337aaf8e0db4fe024608"
          : "bd1f9fed25a8ee2d38543f57909c31a7b65ae21fed56a250a7503495eb656d67",
      );
    },
  );
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
  it.each(["b", "d2", "c", "merged"] as const)(
    "excludes DESIGN vocabulary from panel Details for %s",
    (name) => {
      const view = headerState(name);
      const proposal = view.changes["change-1"]?.proposal;
      if (!proposal) throw new Error("Missing proposal");
      const html = renderToStaticMarkup(
        <>
          <ReviewBody state={view} changeId="change-1" diff={null} showCrossReview={false} />
          <ReviewFacts state={view} proposal={proposal} panel />
        </>,
      );
      expect(html).not.toMatch(
        /invocations|unmatched|static cone bounds|signals|said versus did|probe|equivalence|\bAvailable\b/i,
      );
    },
  );
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

it("mounts cone membership once with missing facts or comparison and retains Details", async () => {
  const { coneState } = await import("./ConeOverlap.fixture");
  for (const missing of ["facts", "comparison", "neither"]) {
    const view = coneState("intersection");
    if (missing === "facts") view.changeFacts = {};
    const current = view.changeFacts["change-1"]?.at(-1);
    if (current && missing === "comparison") {
      current.behavior_diff = null;
      current.measured_cone = null;
      current.review_cards = null;
    }
    const html = renderToStaticMarkup(
      <ReviewBody
        state={view}
        changeId="change-1"
        diff={<div>Diff</div>}
        showCrossReview={false}
        phone
      />,
    );
    expect(html.match(/aria-label="Other pending changes"/g)).toHaveLength(1);
    expect(html).toContain("compute_price</code> is in Agent B");
    if (missing === "neither") expect(html).toContain('aria-label="Details"');
    const proposal = view.changes["change-1"]?.proposal;
    if (!proposal) throw new Error("Missing proposal");
    expect(renderToStaticMarkup(<ReviewFacts state={view} proposal={proposal} />)).toContain(
      "Other pending changes",
    );
  }
});

const attributedView = (problems: string[], complete = false): State => ({
  ...state,
  changeFacts: { [facts.change_id]: [{ ...facts, complete, problems }] },
});
const hash = (html: string) => createHash("sha256").update(html).digest("hex");

it("pins pre-attribution HTML for ordinary and empty problems in both modes", () => {
  const pins = [false, true].flatMap((panel) =>
    [false, true].flatMap((complete) =>
      [
        [],
        facts.problems,
        [
          " configuration attribution: one",
          "Configuration attribution: two",
          "configuration attribution:",
        ],
      ].map((problems) =>
        hash(
          renderToStaticMarkup(
            <ReviewFacts
              state={attributedView(problems, complete)}
              proposal={proposal}
              panel={panel}
            />,
          ),
        ),
      ),
    ),
  );
  expect(pins).toMatchInlineSnapshot(`
    [
      "ec42f5319d6d217226d18c09c4eb8dedbba99b207a8d98f55173bbced013db0f",
      "2fc8db237bd78d7748b89c9ca85bcef75db7c17eebc2fe27d4d74c9e9e198285",
      "37b4a6301e2e1a63f6a4ce6fac33cfdc2008022fa7937a9ed80cd29ccbaedeb2",
      "11617a2409c2e4e1b50a8c7d6013f2a24f5fef9349cdd656eae1cc54aef7946f",
      "260fd8b408c863fa8d79e058decaa91bd6a4fd0e817d57658f73b23931e1c7d6",
      "11617a2409c2e4e1b50a8c7d6013f2a24f5fef9349cdd656eae1cc54aef7946f",
      "58ce42a58beef833d870e68995c75bb76ec38f89db6fcff0d31f0e8b45d40db4",
      "99b35f65581bb729b1f0bcaf6b9695c6c52bcc1cfe12c81d8c313658ac53a1aa",
      "db62974691eb5d9ae7106723e11ecae911a9ca33bd3f9d6db04b73d4558ddcd0",
      "6c8db2d1e662c3ad0512104bfc63e9cd24f7b1aa8a9da3a86a18f0af9305581e",
      "297312fea69ab9e4b2931be4cfe86432c3b5fa2db89522c2428e1b907f1f43cf",
      "6c8db2d1e662c3ad0512104bfc63e9cd24f7b1aa8a9da3a86a18f0af9305581e",
    ]
  `);
});
it("pins the untouched real recording and the no-comparison body without attribution", () => {
  const recorded: ServerEvent[] = readFileSync(
    new URL("../../../fixtures/events/recorded-demo.jsonl", import.meta.url),
    "utf8",
  )
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as ServerEvent);
  const first = recorded.find((event) => event.type === "change.proposed");
  if (first?.type !== "change.proposed") throw new Error("Missing recorded proposal");
  const original = recorded
    .filter((event) => event.seq <= first.seq)
    .reduce(applyEvent, createInitialState());
  expect([
    hash(renderToStaticMarkup(<ReviewFacts state={original} proposal={first.payload} />)),
    ...[false, true].map((phone) =>
      hash(
        renderToStaticMarkup(
          <ReviewBody
            state={attributedView([])}
            changeId={proposal.change_id}
            diff={null}
            showCrossReview={false}
            phone={phone}
          />,
        ),
      ),
    ),
  ]).toMatchInlineSnapshot(`
    [
      "d90283ecf74510c19a8600caeccefb8cfa7a52e9e3ef576996ec3487ca951f2f",
      "efa85e5370363344cff12e5451f1a1b60601fa56efdb92c5a3b97f3019ab3c3f",
      "efa85e5370363344cff12e5451f1a1b60601fa56efdb92c5a3b97f3019ab3c3f",
    ]
  `);
});

it.each([false, true])(
  "renders separate attribution text with panel=%s for both completeness values",
  (panel) => {
    for (const complete of [false, true]) {
      for (const mixed of [false, true]) {
        const notes = [
          "configuration attribution:  unchanged <script>x</script> & path\nnext  ",
          "configuration attribution: second",
          "configuration attribution: second",
        ] as const;
        const problems = mixed
          ? ["first ordinary", notes[0], "last ordinary", ...notes.slice(1)]
          : [...notes];
        const view = attributedView(problems, complete);
        const before = structuredClone(view);
        const html = renderToStaticMarkup(
          <ReviewFacts state={view} proposal={proposal} panel={panel} />,
        );
        expect(html.match(/<p[^>]*>Configuration attribution: /g)).toHaveLength(3);
        expect(html).toContain(
          "Configuration attribution:  unchanged &lt;script&gt;x&lt;/script&gt; &amp; path\nnext  </p>",
        );
        expect(html.indexOf("unchanged")).toBeLessThan(
          html.indexOf("Configuration attribution: second"),
        );
        expect(html).not.toContain("<script>");
        expect(html).not.toContain('role="alert"');
        expect(html.includes("<p>Incomplete: first ordinary; last ordinary</p>")).toBe(
          mixed && !complete,
        );
        expect(html).not.toContain("Incomplete: configuration attribution:");
        if (!mixed) {
          expect(html).not.toContain("Incomplete:");
          if (!panel) expect(html).toContain(">Review signals</summary>");
        }
        expect(view).toEqual(before);
      }
    }
  },
);
it.each([false, true])(
  "exposes closed Details for current attribution alone with phone=%s",
  (phone) => {
    const view = attributedView(["configuration attribution: unchanged"]);
    const render = () =>
      renderToStaticMarkup(
        <ReviewBody
          state={view}
          changeId={proposal.change_id}
          diff={null}
          showCrossReview={false}
          phone={phone}
        />,
      );
    const html = render();
    expect(html).toContain('aria-label="Details"');
    expect(html).toContain("<details>");
    expect(html).not.toContain("<details open");
    expect(html).not.toMatch(/What was compared|Test sequence findings/);
    const current = view.changeFacts[facts.change_id]?.[0];
    if (!current) throw new Error("Missing current facts");
    current.revision++;
    expect(render()).not.toContain('aria-label="Details"');
  },
);
