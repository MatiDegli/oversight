import type { ChangeFacts, ChangeProposed, RunRuleFinding } from "@contracts";
import type { State } from "../state";

export const hasReviewFindings = (facts: ChangeFacts | undefined) =>
  !!facts && (facts.run_findings.length > 0 || facts.shape != null || facts.shape_complete != null);

/** Historical runs belong to the change and agent, but may precede this candidate. */
export function findingExecution(state: State, proposal: ChangeProposed, seq: number) {
  const run = state.testExecutions[seq];
  return run?.change_id === proposal.change_id && run.agent_id === proposal.agent_id
    ? run
    : undefined;
}

export function findingSource(facts: ChangeFacts, finding: RunRuleFinding) {
  const fact = Number.isInteger(finding.fact_index) && facts.integrity[finding.fact_index];
  const kind =
    finding.kind === "loosened"
      ? "assertion_loosened"
      : finding.kind === "skipped"
        ? "skip_added"
        : "test_removed";
  return fact &&
    fact.test_id === finding.test_id &&
    fact.kind === kind &&
    fact.evidence.some((ref) => ref.event_seq === finding.edited_seq)
    ? fact
    : undefined;
}

export const shapeCompletion = (complete: ChangeFacts["shape_complete"]) =>
  complete === true
    ? "Only the bounded structural checks completed. Other issues may remain."
    : complete === false
      ? "Structural checks incomplete; observations may be missing."
      : "Structural check completeness unavailable.";
