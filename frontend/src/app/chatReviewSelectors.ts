import type { ChangeProposed, RuleAssessment, RuleId } from "@contracts";
import type { State } from "../state";
// eslint-disable-next-line no-restricted-imports -- Pure evidence leaves preserve the lazy review boundary.
import { factsForRevision } from "../review/factsForRevision";
// eslint-disable-next-line no-restricted-imports -- Reuse the shared evidence vocabulary.
import { ruleWords } from "../review/evidenceWords";
// eslint-disable-next-line no-restricted-imports -- No review implementation or graph renderer.
import {
  partialLines,
  selectCallerComparison,
  smallestExample,
} from "../review/reviewBodyEvidence";

export const summaryRules = [
  "combined_tests",
  "cross_review",
  "small_change",
  "small_cone",
  "existing_tests_untouched",
  "known_spend",
] as const satisfies readonly RuleId[];

export function sameCandidate(
  a: { change_id: string; revision: number; head_commit: string | null },
  b: ChangeProposed,
) {
  return (
    a.change_id === b.change_id && a.revision === b.revision && a.head_commit === b.head_commit
  );
}

export function changeCost(state: State, changeId: string): number | null {
  const ticks = state.usageTicks.filter((tick) => tick.change_id === changeId);
  return !ticks.length || ticks.some((tick) => tick.cost_usd === null)
    ? null
    : ticks.reduce((total, tick) => total + (tick.cost_usd ?? 0), 0);
}

/** Only the retained current proposal's original timeline entry can receive this card. */
export function selectChatChange(state: State, changeId: string, seq?: number) {
  const change = state.changes[changeId];
  const proposal = change?.proposal;
  if (!proposal || (seq !== undefined && seq !== change.proposalEvent?.seq)) return null;
  const entry = state.decisionEvents.find(
    (event) =>
      event.type === "change.proposed" &&
      event.seq === change.proposalEvent?.seq &&
      sameCandidate(event.payload, proposal) &&
      event.payload.agent_id === proposal.agent_id,
  );
  if (!entry) return null;
  const facts = factsForRevision(state, proposal);
  const assessment = state.decisionEvents.findLast(
    (event) =>
      event.type === "change.assessed" &&
      sameCandidate(event.payload, proposal) &&
      event.payload.agent_id === proposal.agent_id,
  );
  const rules = summaryRules.map(
    (rule): { rule: RuleId; label: string; result: RuleAssessment["result"] | "absent" } => ({
      rule,
      label: ruleWords[rule][1],
      result:
        assessment?.type === "change.assessed"
          ? (assessment.payload.rules.find((r) => r.rule === rule)?.result ?? "absent")
          : "absent",
    }),
  );
  const settled = state.decisionEvents.some(
    (event) =>
      event.type === "change.resolved" &&
      event.payload.change_id === proposal.change_id &&
      event.payload.revision === proposal.revision &&
      // Recordings before head_commit was added resolve the revision without naming its head.
      (!event.payload.head_commit || event.payload.head_commit === proposal.head_commit) &&
      event.payload.agent_id === proposal.agent_id &&
      (event.payload.decision === "rejected" ||
        event.payload.decision === "changes_requested" ||
        event.payload.merge === "merged"),
  );
  const callers = selectCallerComparison(state, changeId);
  return {
    proposal,
    seq: entry.seq,
    ts: entry.ts,
    facts,
    callers,
    examples: callers.differing.map((caller) => ({
      ...caller,
      example: smallestExample(caller.evidence?.rows ?? []),
    })),
    partial: partialLines(state, changeId).filter(
      (item) => !callers.differing.some((caller) => caller.nodeId === item.nodeId),
    ),
    rules,
    assessed: assessment !== undefined,
    passes: rules.filter((r) => r.result === "pass").length,
    waiting: !settled,
    cost: changeCost(state, changeId),
  };
}
