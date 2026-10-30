import { ChevronDown } from "lucide-react";
import type { EvidencePractice as Practice } from "./caseEvidence";
import type { PracticeCase } from "./phoneEvidence";
import { caseEvidence, resolutionText } from "./caseEvidence";
import "./recordedEvidence.css";

type Evidence = ReturnType<typeof caseEvidence>;
export const absenceNote =
  "Missing evidence does not establish a clean review, behavior or safety.";
function TestRuns({ evidence }: { evidence: Evidence }) {
  return (
    <>
      {evidence.tests.status === "present" &&
        evidence.tests.value.map((run, i) => (
          <p key={i}>
            {run.source === "step"
              ? `Step ${String(run.step)}`
              : run.source === "proposal"
                ? "Proposal result"
                : `Execution · ${run.phase ?? "unknown phase"}`}{" "}
            · {run.tests.outcome} · {run.tests.passed} passed · {run.tests.failed} failed ·{" "}
            {run.tests.skipped} skipped · {run.tests.duration_ms} ms
            {run.tests.failed_tests.length > 0 && (
              <span> · {run.tests.failed_tests.join(", ")}</span>
            )}
          </p>
        ))}
      {evidence.executions.status === "absent" && (
        <p>Test execution records: Not recorded for this revision and head.</p>
      )}
      <p>Agent steps belong to this proposal round; their exact tested heads were not recorded.</p>
    </>
  );
}
function Facts({ evidence }: { evidence: Evidence }) {
  if (evidence.facts.status === "absent")
    return <p>Review facts were not recorded for this revision and head.</p>;
  return evidence.facts.value.map((facts, i) => (
    <div key={i}>
      {facts.summary?.map((line, j) => (
        <p key={j}>{line}</p>
      ))}
      <p>
        {facts.edited_paths.length} edited paths · {facts.integrity.length} integrity observations ·{" "}
        {facts.added_tests.length} added-test probes
      </p>
      <p>
        Behavior comparisons:{" "}
        {facts.behavior_diff === null ? "Not recorded" : facts.behavior_diff.length}. Measured
        callers: {facts.measured_cone === null ? "Not recorded" : facts.measured_cone.length}.
      </p>
      {facts.problems.map((problem, j) => (
        <p key={j}>{problem}</p>
      ))}
    </div>
  ));
}
function Reviews({ evidence }: { evidence: Evidence }) {
  if (evidence.reviews.status === "absent")
    return <p>Cross-review was not recorded for this revision and head.</p>;
  return evidence.reviews.value.map((review, i) => (
    <div key={i}>
      <p>
        {review.reviewer_model} · {review.reviewer_provider} · {review.verdict.replaceAll("_", " ")}{" "}
        · {review.complete ? "complete" : "incomplete"}
      </p>
      {review.findings.map((finding, j) => (
        <p key={j}>
          {finding.severity ?? "Unclassified"} · {finding.text}
          {finding.path
            ? ` · ${finding.path}${finding.line ? `:${String(finding.line)}` : ""}`
            : ""}
        </p>
      ))}
      {review.problems.map((problem, j) => (
        <p key={j}>{problem}</p>
      ))}
    </div>
  ));
}
function History({ evidence }: { evidence: Evidence }) {
  if (evidence.history.status === "absent") return <p>Review history was not recorded.</p>;
  return evidence.history.value.map((event) => (
    <div key={event.seq}>
      <p>
        Revision {event.value.revision} ·{" "}
        {event.type === "proposal" ? "Proposed" : resolutionText(event.value)}
      </p>
      {event.type === "resolution" && (
        <>
          <p>{event.value.comment ? `Comment: ${event.value.comment}` : "Comment: Not recorded"}</p>
          {event.value.merge_commit && (
            <p className="evidence-hash">Merge commit: {event.value.merge_commit}</p>
          )}
          {event.value.conflict && (
            <p>
              Conflict · {event.value.conflict.kind} · {event.value.conflict.paths.join(", ")}
            </p>
          )}
        </>
      )}
    </div>
  ));
}
export function RecordedEvidence({ practice, entry }: { practice: Practice; entry: PracticeCase }) {
  const evidence = caseEvidence(practice, entry);
  const tests = evidence.tests.status === "present" ? evidence.tests.value : [];
  const runs = tests.filter((run) => run.source === "step");
  const last = runs.at(-1)?.tests;
  const history = evidence.history.status === "present" ? evidence.history.value : [];
  const resolution = history.filter((event) => event.type === "resolution").at(-1);
  const rows = [
    {
      label: "Agent’s test runs",
      preview: last
        ? `${String(runs.length)} ${runs.length === 1 ? "run" : "runs"}, last ${String(last.passed)} of ${String(last.passed + last.failed)} ${last.outcome}`
        : "Not recorded",
      content: <TestRuns evidence={evidence} />,
    },
    {
      label: "Review facts",
      preview:
        evidence.facts.status === "present"
          ? evidence.facts.value[0].complete
            ? "Recorded"
            : "Recorded · incomplete"
          : "Not recorded",
      content: <Facts evidence={evidence} />,
    },
    {
      label: "Cross-review",
      preview:
        evidence.reviews.status === "present"
          ? `${String(evidence.reviews.value.length)} recorded`
          : "Not recorded",
      content: <Reviews evidence={evidence} />,
    },
    {
      label: "Review history",
      preview:
        resolution?.type === "resolution" ? resolutionText(resolution.value) : "Not recorded",
      content: <History evidence={evidence} />,
    },
  ];
  return (
    <div className="recorded-evidence">
      <h3>Recorded evidence</h3>
      <div className="evidence-rows">
        {rows.map((row) => (
          <details className="evidence-row" key={row.label}>
            <summary>
              <span>
                {row.label} · {row.preview}
              </span>
              <ChevronDown size={14} />
            </summary>
            <div className="evidence-content">{row.content}</div>
          </details>
        ))}
      </div>
    </div>
  );
}
