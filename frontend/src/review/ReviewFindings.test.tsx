import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import {
  findingFacts,
  findingState,
  runKinds,
  shapeKinds,
  shapeStates,
  references,
} from "./ReviewFindings.fixture";
import { ReviewFacts, detectorLine } from "./ReviewFacts";
import { ReviewBody } from "./ReviewBody";
import { comparisonPreview, selectReviewBody } from "./reviewBodyEvidence";
import { approvalLocked } from "./answers";
import { availableFacts, withMeasuredFacts } from "./measuredCallers.fixture";
import { ShapeFindings } from "./ShapeFindings";

function render(state = findingState(), body = false) {
  const proposal = state.changes["change-1"]?.proposal;
  if (!proposal) throw new Error("Missing candidate");
  return renderToStaticMarkup(
    body ? (
      <ReviewBody state={state} changeId="change-1" diff={<p>Diff</p>} showCrossReview={false} />
    ) : (
      <ReviewFacts state={state} proposal={proposal} panel />
    ),
  );
}
it.each([...runKinds, "all"] as const)(
  "retains published sequence and local support: %s",
  (name) => {
    const state = findingState(name),
      html = render(state);
    const facts = findingFacts(state);
    for (const finding of facts.run_findings) {
      expect(html).toContain(finding.test_id);
      for (const label of [
        "Kind: " + finding.kind,
        "Failure #10",
        "Edit #12",
        "Later execution #14",
        "tests/test_pricing.py",
        "Observed " + finding.kind,
      ])
        expect(html).toContain(label);
      expect(html).toContain(`line ${String(17 + finding.fact_index)}`);
      expect(html).toContain("recorded execution (agent; failed suite)");
      if (finding.kind === "loosened")
        expect(html).toContain("assertion loosening, then a later pass.");
      else expect(html).toContain("This does not establish that the test executed or passed.");
    }
    if (name !== "loosened" && name !== "all") expect(html).not.toContain("a later pass.");
    for (const ref of references) {
      if (ref.git_object) expect(html).toContain(ref.git_object);
      if (ref.event_seq) expect(html).toContain(`Event #${String(ref.event_seq)}`);
    }
    expect(html).not.toMatch(/<a |intent|cheating|recovery|caused/);
  },
);
it.each(["missing", "change", "agent", "index", "identity", "source-kind", "source-event"])(
  "keeps identifiers with unavailable %s support",
  (bad) => {
    const state = findingState("removed"),
      facts = findingFacts(state);
    const run = state.testExecutions[10],
      finding = facts.run_findings[0];
    if (!run || !finding) throw new Error("Missing fixture");
    if (bad === "missing") delete state.testExecutions[10];
    if (bad === "change") run.change_id = "other";
    if (bad === "agent") run.agent_id = "other";
    if (bad === "index") finding.fact_index = 999;
    if (bad === "identity") finding.test_id = "other::case";
    const source = facts.integrity[0];
    if (source && bad === "source-kind") source.kind = "parse_error";
    if (source && bad === "source-event") source.evidence = [];
    const html = render(state);
    expect(html).toContain("Failure #10");
    expect(html).toContain("Edit #12");
    expect(html).toContain("Later execution #14");
    expect(html).toContain(
      ["index", "identity", "source-kind", "source-event"].includes(bad)
        ? "Supporting source observation unavailable"
        : "execution detail unavailable",
    );
    expect(html).not.toContain("href=");
  },
);
it.each(shapeStates)("distinguishes whole-analysis state: %s", (name) => {
  const facts = findingFacts(findingState(name));
  const html = renderToStaticMarkup(<ShapeFindings facts={facts} />);
  expect(html).toContain(
    facts.shape_complete === true
      ? "Only the bounded structural checks completed"
      : facts.shape_complete === false
        ? "Structural checks incomplete"
        : "completeness unavailable",
  );
  if (facts.shape == null) expect(html).toContain("analysis was not recorded");
  else if (!facts.shape.length) expect(html).toContain("No structural observations retained");
  else for (const fact of facts.shape) expect(html).toContain(fact.message);
});
it.each(shapeKinds)("renders structural kind and all references: %s", (name) => {
  const html = render(findingState(name));
  expect(html).toContain("shop/pricing.py");
  for (const ref of references) if (ref.git_object) expect(html).toContain(ref.git_object);
  expect(html).toContain("Event #12");
  if (name === "duplicate_logic")
    expect(html).toContain("Structure alone does not establish the same behavior.");
});
it("escapes and retains long text, optional nodes and empty reference lists", () => {
  const state = findingState("all"),
    facts = findingFacts(state),
    first = facts.shape?.[0];
  if (!first) throw new Error("Missing shape");
  first.path = "folder/".repeat(60) + "<img>.py";
  first.message = "<script>alert('sample')</script> " + "observation ".repeat(80);
  first.node_id = "module:<node>";
  first.evidence = [];
  const html = render(state);
  expect(html).toContain(renderToStaticMarkup(<code>{first.path}</code>));
  expect(html).toContain("&lt;script&gt;");
  expect(html).toContain("module:&lt;node&gt;");
  expect(html).toContain("No supporting references recorded.");
  expect(html).not.toContain("<img>");
});
it("selects latest whole matching facts and clears without fallback", () => {
  const state = findingState(),
    facts = findingFacts(state);
  const history = (state.changeFacts["change-1"] = [
    facts,
    { ...facts, revision: 99 },
    { ...facts, head_commit: "wrong" },
    { ...facts, change_id: "wrong" },
  ]);
  expect(render(state)).toContain("Test sequence findings");
  history.push({ ...facts, run_findings: [], shape: null, shape_complete: null });
  expect(render(state)).not.toContain("Test sequence findings");
  expect(render(state)).toContain("analysis was not recorded");
  history.splice(
    0,
    history.length,
    { ...facts, revision: 99 },
    { ...facts, head_commit: "wrong" },
    { ...facts, change_id: "wrong" },
  );
  expect(render(state)).not.toContain("Test sequence findings");
});
it("mounts only recorded findings without comparison and keeps extension placement", () => {
  for (const name of ["loosened", "empty-incomplete", "completeness-unavailable"] as const) {
    const html = render(findingState(name, false), true);
    expect(html).not.toContain('aria-label="What was compared"');
    expect(html).toContain('aria-label="Details"');
    expect(html.indexOf('aria-label="Details"')).toBeGreaterThan(
      html.indexOf('aria-label="New-parameter calls"'),
    );
  }
  expect(render(findingState("not-recorded", false), true)).not.toContain('aria-label="Details"');
  expect(render(findingState("all"), true)).toMatch(
    /New-parameter calls[\s\S]*aria-label="Details"/,
  );
});
it("keeps source, execution, added-test, comparison, caller, question and decision facts unchanged", () => {
  const state = findingState(),
    facts = findingFacts(state);
  facts.test_runs = [10, 14];
  facts.added_tests = [
    {
      test_id: "tests/test_added.py::test_added",
      before_seq: 10,
      after_seq: 14,
      result: "fails_before_passes_after",
    },
  ];
  const before = render(state);
  const original = selectReviewBody(state, "change-1"),
    preview = comparisonPreview(state, "change-1");
  const counts = [
    detectorLine(facts),
    facts.test_runs,
    facts.added_tests,
    state.testExecutions,
    approvalLocked(facts.review_cards ?? [], {}),
  ];
  facts.run_findings = [];
  facts.shape = [];
  facts.shape_complete = true;
  const after = selectReviewBody(state, "change-1");
  expect([after.kind, after.summary, after.callers, after.card, after.facts?.review_cards]).toEqual(
    [
      original.kind,
      original.summary,
      original.callers,
      original.card,
      original.facts?.review_cards,
    ],
  );
  expect(comparisonPreview(state, "change-1")).toBe(preview);
  expect([
    detectorLine(facts),
    facts.test_runs,
    facts.added_tests,
    state.testExecutions,
    approvalLocked(facts.review_cards ?? [], {}),
  ]).toEqual(counts);
  const strip = (html: string) =>
    html
      .replace(
        /<section aria-label="Test sequence findings"[\s\S]*?<div class="mt-3.5">/,
        '<div class="mt-3.5">',
      )
      .replace(
        /<section aria-label="Structural observations"[\s\S]*?<div class="mt-3.5">/,
        '<div class="mt-3.5">',
      );
  expect(strip(before)).toBe(strip(render(state)));
});

it("preserves actual caller measurements and review kind when findings change", () => {
  const supplied = findingFacts(findingState());
  const state = withMeasuredFacts({
    ...availableFacts,
    run_findings: supplied.run_findings,
    shape: supplied.shape,
    shape_complete: false,
  });
  const before = selectReviewBody(state, "change-1");
  expect(before.callers.measured).toBeGreaterThan(0);
  const facts = findingFacts(state);
  facts.run_findings = [];
  facts.shape = null;
  const after = selectReviewBody(state, "change-1");
  expect([after.kind, after.callers, after.summary]).toEqual([
    before.kind,
    before.callers,
    before.summary,
  ]);
});
