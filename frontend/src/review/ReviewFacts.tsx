import type { AddedTestProbe, ChangeFacts, ChangeProposed, SuiteExecution } from "@contracts";

import type { State } from "../state";
import { BehaviorDiff } from "./BehaviorDiff";
import { behaviorNotRecorded } from "./behaviorEvidence";
import { MeasuredCallers } from "./MeasuredCallers";
import { factsForRevision } from "./factsForRevision";

/** The same neutral vocabulary as changes/_reviewfacts.py; no policy assessment. */
export function detectorLine(facts: ChangeFacts): string {
  if (facts.problems.some((p) => p.startsWith("detector unknown:"))) {
    return "Test facts unknown: detector analysis incomplete";
  }
  if (facts.integrity.some((f) => f.kind === "parse_error")) {
    return "Test facts unknown: parse error";
  }
  const phrases = [
    ["test_removed", "removed", "removed"],
    ["assertion_loosened", "check changed", "checks changed"],
    ["expected_value_changed", "expected value changed", "expected values changed"],
    ["skip_added", "skip added", "skips added"],
  ] as const;
  const parts = phrases.flatMap(([kind, singular, plural]) => {
    const count = facts.integrity.filter((f) => f.kind === kind).length;
    return count ? [`${String(count)} ${count === 1 ? singular : plural}`] : [];
  });
  return `Tests in this change: ${parts.join(", ") || "0 recorded test changes"}`;
}

/** Recognize the explicit summary sentences accepted by the Python review core. */
function summaryClaims(summary: string | null, paths: string[]) {
  const sentences = (summary ?? "")
    .split(/[.!?](?:\s+|$)|\n/)
    .map((s) => s.trim().replace(/^- /, ""))
    .filter(Boolean);
  function booleanClaim(yes: RegExp, no: RegExp): boolean | null {
    const positive = sentences.some((s) => yes.test(s));
    const negative = sentences.some((s) => no.test(s));
    return positive === negative ? null : positive;
  }
  const mentioned: string[] = [];
  let named = false;
  const noFiles = sentences.some((s) => /^(?:no files changed|i changed no files)$/i.test(s));
  for (const sentence of sentences) {
    const list =
      /^(?:(?:i )?(?:updated|edited|modified|changed|touched|added|removed|deleted|fixed) |files(?: (?:changed|touched))?:\s*)(.+)$/i.exec(
        sentence,
      )?.[1];
    if (list === undefined) continue;
    const entries = list.split(/,\s*|\s+and\s+/).map((p) => p.trim().replace(/^`|`$/g, ""));
    if (entries.every((p) => paths.includes(p))) {
      named = true;
      mentioned.push(...entries);
    }
  }
  return {
    files:
      named && noFiles ? null : named ? [...new Set(mentioned)].toSorted() : noFiles ? [] : null,
    ran: booleanClaim(
      /^(?:(?:i )?ran (?:the )?tests|(?:the )?tests were run)$/i,
      /^(?:(?:i )?did not run (?:the )?tests|(?:the )?tests were not run)$/i,
    ),
    passed: booleanClaim(
      /^(?:all |the )?tests (?:pass|passed)$/i,
      /^(?:all |the )?tests (?:failed|fail|did not pass)$/i,
    ),
  };
}

export function discrepancies(
  facts: ChangeFacts,
  executions: SuiteExecution[],
  summary: string | null = null,
): string[] {
  const declared = facts.declared;
  const parsed = summaryClaims(summary, [...facts.edited_paths, ...(declared?.files ?? [])]);
  const lines: string[] = declared ? [] : ["Finish declaration unavailable"];
  const ran = declared?.tests_run ?? parsed.ran;
  const files = declared?.files ?? parsed.files;
  const agentRuns = executions.filter((e) => e.phase === "agent");
  if (ran === true && agentRuns.length === 0) {
    lines.push("Said tests were run; no agent run_tests call recorded");
  }
  if (ran === false && agentRuns.length > 0) {
    lines.push("Said tests were not run; agent run_tests calls recorded");
  }
  const lastRun = agentRuns.at(-1);
  if (parsed.passed === true && lastRun && lastRun.suite.outcome !== "passed") {
    lines.push("Said tests pass; last agent run did not pass");
  }
  if (files !== null) {
    for (const path of files) {
      if (!facts.edited_paths.includes(path)) lines.push(`Named ${path}; no touch recorded`);
    }
    for (const path of facts.edited_paths) {
      if (!files.includes(path)) lines.push(`Did not name ${path}; touch recorded`);
    }
  }
  for (const id of declared?.tests ?? []) {
    const observed = agentRuns.flatMap((e) => e.suite.cases ?? []).some((c) => c.test_id === id);
    if (!observed) lines.push(`Named test ${id}; execution evidence unavailable`);
  }
  return lines;
}

function executionLine(seq: number, execution: SuiteExecution): string {
  const suite = execution.suite;
  const failed = [
    ...new Set([
      ...suite.failed_tests,
      ...(suite.cases ?? [])
        .filter((c) => c.outcome === "failed" || c.outcome === "error")
        .map((c) => c.test_id),
    ]),
  ];
  return `Tests #${String(seq)} (${execution.phase}): ${suite.outcome}; ${String(suite.passed)} passed, ${String(suite.failed)} failed, ${String(suite.skipped)} skipped${failed.length ? `; failed ids: ${failed.join(", ")}` : ""}${suite.cases_complete === true ? "" : "; per-test evidence incomplete"}`;
}

const probeLabels: Record<AddedTestProbe["result"], string> = {
  fails_before_passes_after: "fails before, passes after",
  passes_before_and_after: "passes before and after",
  fails_after: "fails after",
  not_collected_after: "not collected after",
  skipped: "skipped",
  unknown: "unknown",
};

/** Compact, scrollable evidence; events never move focus or open it automatically. */
export function ReviewFacts({
  state,
  proposal,
  scrollable = true,
  panel = false,
}: {
  state: State;
  proposal: ChangeProposed;
  /** The summary review shares one scroll region with the rest of its context. */
  scrollable?: boolean;
  panel?: boolean;
}) {
  const facts = factsForRevision(state, proposal);
  if (!facts)
    return (
      <section className="border-b border-page-border px-4.5 py-2.5">
        <p className="text-sm text-muted">
          Facts incomplete: facts for this revision are unavailable. {behaviorNotRecorded}
        </p>
        <MeasuredCallers state={state} changeId={proposal.change_id} detailsOnly={panel} />
      </section>
    );
  const executions = facts.test_runs
    .map((seq) => state.testExecutions[seq])
    .filter(
      (e): e is SuiteExecution =>
        e !== undefined && e.change_id === proposal.change_id && e.agent_id === proposal.agent_id,
    );
  return (
    <section
      aria-label="Review signals"
      className={
        panel
          ? "review-details text-sm"
          : "shrink-0 border-b border-page-border px-4.5 py-2.5 text-sm"
      }
    >
      <details open={panel ? undefined : true}>
        <summary className="cursor-pointer rounded-label font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text">
          {panel
            ? "Details"
            : `Review signals · ${facts.complete ? "Recorded facts" : "Incomplete"}`}
        </summary>
        <div
          className={`mt-2 break-words text-muted${scrollable ? " max-h-48 overflow-y-auto" : ""}`}
        >
          {!panel && <p>Signals for your review. You decide.</p>}
          <p>
            Size: {proposal.files.length} files, +{proposal.lines_added}/−{proposal.lines_removed}{" "}
            lines
          </p>
          <>
            <ul aria-label="Said versus did">
              {discrepancies(facts, executions, proposal.summary).map((line) => (
                <li key={line}>Said versus did: {line}</li>
              ))}
            </ul>
            {facts.test_runs.length === 0 && <p>Tests: no execution evidence recorded</p>}
            {facts.test_runs.map((seq) => {
              const execution = state.testExecutions[seq];
              return (
                <p key={seq}>
                  {execution?.change_id === proposal.change_id &&
                  execution.agent_id === proposal.agent_id
                    ? executionLine(seq, execution)
                    : `Tests #${String(seq)}: evidence unavailable`}
                </p>
              );
            })}
            <p>{detectorLine(facts)}</p>
            {facts.added_tests.length > 0 && (
              <ul aria-label="Added test signals">
                {facts.added_tests.map((probe) => (
                  <li key={probe.test_id}>
                    <code className="break-all font-mono">{probe.test_id}</code>:{" "}
                    {probeLabels[probe.result]}
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-3.5">
              <BehaviorDiff state={state} changeId={proposal.change_id} detailsOnly={panel} />
              {facts.behavior_diff == null && <p>{behaviorNotRecorded}</p>}
            </div>
            {!facts.complete && <p>Incomplete: {facts.problems.join("; ")}</p>}
            <div className="pt-(--spacing-lg)">
              <MeasuredCallers state={state} changeId={proposal.change_id} detailsOnly={panel} />
            </div>
          </>
        </div>
      </details>
    </section>
  );
}
