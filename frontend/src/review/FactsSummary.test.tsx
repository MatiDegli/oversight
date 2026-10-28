import { readFileSync } from "node:fs";

import type { ChangeFacts, ChangeProposed, ServerEvent } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { applyEvent, createInitialState } from "../state";
import { FactsSummary } from "./FactsSummary";
import { ReviewPanel } from "./ReviewPanel";

vi.mock("../editor", () => ({ EditorDiff: () => <div>Inline diff</div> }));

const state = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent)
  .filter((event) => event.seq <= 20)
  .reduce(applyEvent, createInitialState());
const recordedProposal = state.changes["change-1"]?.proposal;
if (!recordedProposal) throw new Error("Missing proposal");
const proposal = recordedProposal;

const lines = [
  "Task: Cap the combined discount at 30%",
  "Changed: 2 files, 3 nodes; tests added: 1; tests run: 12 (12 passed, 0 failed, 0 skipped; passed)",
  "Other results: not compared; cost $0.0021",
];
const facts: ChangeFacts = {
  change_id: proposal.change_id,
  revision: proposal.revision,
  base_commit: proposal.base_commit,
  head_commit: proposal.head_commit,
  main_commit: proposal.base_commit,
  candidate_tree: "tree",
  declared: null,
  edited_paths: ["shop/pricing.py", "tests/test_pricing.py"],
  test_runs: [],
  integrity: [],
  added_tests: [],
  run_findings: [],
  zone: null,
  reach: null,
  complete: true,
  problems: [],
  summary: lines,
  behavior_diff: null,
  measured_cone: null,
  review_cards: null,
  shape: null,
  shape_complete: null,
};
const label = "Recorded facts summary";

function panel(records: ChangeFacts[], target: ChangeProposed = proposal) {
  const view = applyEvent(
    { ...state, changeFacts: { [target.change_id]: records } },
    {
      seq: state.lastSeq + 1,
      ts: "2026-10-28T12:00:00Z",
      type: "change.proposed",
      payload: target,
    },
  );
  return renderToStaticMarkup(
    <ReviewPanel state={view} changeId={target.change_id} onClose={() => undefined} />,
  );
}

function block(html: string) {
  return html.match(/<section aria-label="Recorded facts summary"[\s\S]*?<\/section>/)?.[0];
}

describe("recorded facts summary", () => {
  it("renders the three server lines in order above the existing facts block", () => {
    const html = panel([facts]);
    const summary = block(html);
    expect(summary).toBeDefined();
    expect(summary?.match(/<p [^>]*>(.*?)<\/p>/g)?.map((p) => p.replace(/<[^>]*>/g, ""))).toEqual(
      lines,
    );
    expect(html.indexOf(`aria-label="${label}"`)).toBeLessThan(
      html.indexOf('aria-label="Review signals"'),
    );
  });

  it.each([
    ["null summary", { ...facts, summary: null }],
    ["missing facts", undefined],
  ] as const)("renders nothing for %s, including no label", (_, record) => {
    expect(renderToStaticMarkup(<FactsSummary facts={record} />)).toBe("");
    const html = panel(record ? [record] : []);
    expect(html).not.toContain(label);
    for (const line of lines) expect(html).not.toContain(line);
  });

  it.each([
    ["newer revision", { ...proposal, revision: proposal.revision + 1 }],
    ["different head", { ...proposal, head_commit: "new-head" }],
  ] as const)("never shows earlier facts for a proposal with a %s", (_, target) => {
    const html = panel([facts], target);
    expect(html).not.toContain(label);
    for (const line of lines) expect(html).not.toContain(line);
  });

  it("uses the latest matching facts and never falls back to an older summary", () => {
    const old = { ...facts, summary: ["Earlier facts"] };
    expect(block(panel([old, facts]))).not.toContain("Earlier facts");
    expect(block(panel([old, facts]))).toContain(lines[2]);
    expect(panel([old, { ...facts, summary: null }])).not.toContain(label);
  });

  it("keeps the agent's report outside the labeled summary block", () => {
    const report = "Agent report: I changed everything and all tests passed.";
    const html = panel([facts], { ...proposal, summary: report });
    expect(html).toContain(report);
    expect(block(html)).toBeDefined();
    expect(block(html)).not.toContain(report);
    expect(html.indexOf(report)).toBeGreaterThan(html.indexOf('aria-label="Review signals"'));
  });

  it("preserves opaque text and whitespace, escapes markup and keeps unknowns neutral", () => {
    const opaque = [
      "Task:  **keep this** <script>alert(1)</script>",
      "Other results: cost unknown",
    ];
    const html = renderToStaticMarkup(<FactsSummary facts={{ ...facts, summary: opaque }} />);
    expect(html).toContain("Task:  **keep this** &lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).toContain(opaque[1]);
    expect(html).not.toMatch(/<script>|<strong>|text-passed|text-failed|text-agent/);
  });
});
