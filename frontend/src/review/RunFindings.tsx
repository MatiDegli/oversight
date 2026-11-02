import type { ChangeFacts, ChangeProposed, RunRuleFinding } from "@contracts";
import type { State } from "../state";
import { findingExecution, findingSource } from "./reviewFindings";
import { FindingReferences } from "./ShapeFindings";

const weakening: Record<RunRuleFinding["kind"], string> = {
  loosened: "assertion loosening",
  removed: "test removal",
  skipped: "added skip",
  deleted: "test deletion",
};
export function RunFindings({
  facts,
  state,
  proposal,
}: {
  facts: ChangeFacts;
  state: State;
  proposal: ChangeProposed;
}) {
  if (!facts.run_findings.length) return null;
  return (
    <section aria-label="Test sequence findings" className="review-findings">
      <h3>Test sequence findings</h3>
      <ul className="finding-list">
        {facts.run_findings.map((finding, index) => {
          const source = findingSource(facts, finding);
          return (
            <li key={index}>
              <p>
                <code>{finding.test_id}</code>
              </p>
              <p className="finding-kind">Kind: {finding.kind}</p>
              <p>
                Recorded failure, then {weakening[finding.kind]}, then{" "}
                {finding.kind === "loosened"
                  ? "a later pass."
                  : "later absence of failure. This does not establish that the test executed or passed."}
              </p>
              <ul className="finding-sequence">
                {(
                  [
                    ["Failure", finding.failed_seq],
                    ["Edit", finding.edited_seq],
                    ["Later execution", finding.passed_seq],
                  ] as const
                ).map(([label, seq]) => {
                  const run = findingExecution(state, proposal, seq);
                  return (
                    <li key={label}>
                      {label} #{seq}:{" "}
                      {label === "Edit"
                        ? "recorded edit"
                        : run
                          ? `recorded execution (${run.phase}; ${run.suite.outcome} suite)`
                          : "execution detail unavailable"}
                    </li>
                  );
                })}
              </ul>
              {source ? (
                <div className="finding-source">
                  <p>
                    Source <code>{source.path}</code>, line {source.line}
                  </p>
                  <p>{source.text}</p>
                  <FindingReferences evidence={source.evidence} />
                </div>
              ) : (
                <p className="finding-bound">
                  Supporting source observation unavailable (index {finding.fact_index}).
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
