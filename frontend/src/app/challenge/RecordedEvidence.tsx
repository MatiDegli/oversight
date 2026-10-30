import { ChevronDown } from "lucide-react";
import type { EvidencePractice as Practice } from "./caseEvidence";
import type { PracticeCase } from "./phoneEvidence";
import { caseEvidence, resolutionText } from "./caseEvidence";
import { sentenceList } from "./evidenceCopy";
import "./recordedEvidence.css";

type Evidence = ReturnType<typeof caseEvidence>;
const words = (value: string) => value.replaceAll("_", " ");
const severityLabels = { BLOCKING: "Blocking", "SHOULD-FIX": "Should-fix", NIT: "Nit" };
export const absenceNote =
  "Missing evidence does not establish a clean review, behavior or safety.";
function TestRuns({ evidence }: { evidence: Evidence }) {
  const runs = evidence.tests.status === "present" ? evidence.tests.value : [];
  const steps = runs.some((run) => run.source === "step");
  return (
    <>
      <div className="evidence-runs">
        {runs.map((run, i) => (
          <p key={i}>
            {run.source === "step"
              ? `Step ${String(run.step)}`
              : run.source === "proposal"
                ? "Proposal"
                : `Execution (${words(run.phase ?? "unknown phase")})`}
            : {run.tests.outcome}, {run.tests.passed} passed
            {run.tests.failed ? `, ${String(run.tests.failed)} failed` : ""}
            {run.tests.skipped ? `, ${String(run.tests.skipped)} skipped` : ""}, in{" "}
            {run.tests.duration_ms} ms.
            {run.tests.failed_tests.length > 0 && (
              <span> Failed tests: {run.tests.failed_tests.join(", ")}.</span>
            )}
          </p>
        ))}
      </div>
      {steps ? (
        <p>
          Agent steps belong to this proposal round; their exact tested heads
          {evidence.executions.status === "absent" ? " and test execution records" : ""} were not
          recorded.
        </p>
      ) : (
        evidence.executions.status === "absent" && (
          <p>Test execution records were not recorded for this revision and head.</p>
        )
      )}
    </>
  );
}
function Facts({ evidence }: { evidence: Evidence }) {
  if (evidence.facts.status === "absent") return null;
  const records = evidence.facts.value;
  const missing = records.flatMap((facts, i) => {
    const record = records.length > 1 ? ` in record ${String(i + 1)}` : "";
    return [
      ...(facts.behavior_diff === null ? [`behavior comparisons${record}`] : []),
      ...(facts.measured_cone === null ? [`measured callers${record}`] : []),
    ];
  });
  return (
    <>
      {records.map((facts, i) => {
        const counts = [
          ...(facts.edited_paths.length
            ? [`${String(facts.edited_paths.length)} edited paths`]
            : []),
          ...(facts.integrity.length
            ? [`${String(facts.integrity.length)} integrity observations`]
            : []),
          ...(facts.added_tests.length
            ? [`${String(facts.added_tests.length)} added-test probes`]
            : []),
          ...(facts.behavior_diff?.length
            ? [`${String(facts.behavior_diff.length)} behavior comparisons`]
            : []),
          ...(facts.measured_cone?.length
            ? [`${String(facts.measured_cone.length)} measured callers`]
            : []),
        ];
        return (
          <div key={i}>
            {facts.summary?.map((line, j) => (
              <p key={j}>{line}</p>
            ))}
            {counts.length > 0 && <p>{sentenceList(counts)} were recorded.</p>}
            {facts.behavior_diff?.length === 0 && (
              <p>The recorded behavior comparison list is empty.</p>
            )}
            {facts.measured_cone?.length === 0 && <p>The recorded caller list is empty.</p>}
            {facts.problems.map((problem, j) => (
              <p key={j}>{problem}</p>
            ))}
          </div>
        );
      })}
      {missing.length > 0 && <p>Not recorded: {sentenceList(missing)}.</p>}
    </>
  );
}
function Reviews({ evidence }: { evidence: Evidence }) {
  if (evidence.reviews.status === "absent") return null;
  return evidence.reviews.value.map((review, i) => (
    <div key={i}>
      <p>
        {review.reviewer_model} ({review.reviewer_provider}) recorded a {words(review.verdict)}{" "}
        review, {review.complete ? "complete" : "incomplete"}.
      </p>
      {review.findings.map((finding, j) => (
        <p key={j}>
          {finding.severity ? severityLabels[finding.severity] : "Unclassified"}: {finding.text}
          {finding.path && (
            <>
              {" "}
              in{" "}
              <code>
                {finding.path}
                {finding.line ? `:${String(finding.line)}` : ""}
              </code>
            </>
          )}
        </p>
      ))}
      {review.problems.map((problem, j) => (
        <p key={j}>{problem}</p>
      ))}
    </div>
  ));
}
function History({ evidence }: { evidence: Evidence }) {
  if (evidence.history.status === "absent") return null;
  const missing = evidence.history.value.flatMap((event) =>
    event.type === "resolution"
      ? [
          ...(!event.value.decided_by
            ? [`decision maker for revision ${String(event.value.revision)}`]
            : []),
          ...(event.value.comment === null
            ? [`comment for revision ${String(event.value.revision)}`]
            : []),
        ]
      : [],
  );
  return (
    <>
      {evidence.history.value.map((event) => (
        <div key={event.seq}>
          <p>
            {event.value.revision
              ? `Revision ${String(event.value.revision)}`
              : "Before the first proposal"}
            : {event.type === "proposal" ? "Proposed" : resolutionText(event.value, false)}.
          </p>
          {event.type === "resolution" && (
            <>
              {event.value.comment !== null && <p>Comment: {event.value.comment}</p>}
              {event.value.merge_commit && (
                <p>
                  Merge commit: <code className="evidence-hash">{event.value.merge_commit}</code>
                </p>
              )}
              {event.value.conflict && (
                <p>
                  Conflict ({event.value.conflict.kind})
                  {event.value.conflict.paths.length > 0
                    ? ` in ${event.value.conflict.paths.join(", ")}`
                    : ""}
                  .
                </p>
              )}
            </>
          )}
        </div>
      ))}
      {missing.length > 0 && <p>Not recorded: {sentenceList(missing)}.</p>}
    </>
  );
}
export function RecordedEvidence({ practice, entry }: { practice: Practice; entry: PracticeCase }) {
  const evidence = caseEvidence(practice, entry);
  const runs =
    evidence.tests.status === "present"
      ? evidence.tests.value.filter((run) => run.source !== "proposal")
      : [];
  const last = runs.at(-1)?.tests;
  const history = evidence.history.status === "present" ? evidence.history.value : [];
  const resolution = history.filter((event) => event.type === "resolution").at(-1);
  const rows = [
    {
      label: "Agent’s test runs",
      present: runs.length > 0,
      preview: last
        ? `${String(runs.length)} ${runs.length === 1 ? "run" : "runs"}, last ${last.passed ? `${String(last.passed)} of ${String(last.passed + last.failed)} ` : ""}${last.outcome}`
        : "Recorded",
      content: <TestRuns evidence={evidence} />,
    },
    {
      label: "Review facts",
      present: evidence.facts.status === "present",
      preview:
        evidence.facts.status === "present" && evidence.facts.value.some((facts) => !facts.complete)
          ? "Recorded, incomplete"
          : "Recorded",
      content: <Facts evidence={evidence} />,
    },
    {
      label: "Cross-review",
      present: evidence.reviews.status === "present",
      preview:
        evidence.reviews.status === "present"
          ? `${String(evidence.reviews.value.length)} recorded`
          : "Recorded",
      content: <Reviews evidence={evidence} />,
    },
    {
      label: "Review history",
      present: evidence.history.status === "present",
      preview:
        resolution?.type === "resolution" ? resolutionText(resolution.value) : "Proposal recorded",
      content: <History evidence={evidence} />,
    },
  ];
  const recorded = rows.filter((row) => row.present);
  const absent = rows.filter((row) => !row.present).map((row) => row.label.toLowerCase());
  return (
    <div className="recorded-evidence">
      <h3>Recorded evidence</h3>
      {recorded.length > 0 && (
        <div className="evidence-rows">
          {recorded.map((row) => (
            <details className="evidence-row" key={row.label}>
              <summary>
                <span>
                  <span>{row.label}</span> <span className="evidence-preview">{row.preview}</span>
                </span>
                <ChevronDown size={14} />
              </summary>
              <div className="evidence-content">{row.content}</div>
            </details>
          ))}
        </div>
      )}
      {absent.length > 0 && (
        <p className="evidence-absence">Not recorded for this revision: {sentenceList(absent)}.</p>
      )}
    </div>
  );
}
