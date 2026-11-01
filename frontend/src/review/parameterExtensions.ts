import type { ChangeProposed, ParameterExtensionDiff } from "@contracts";
import type { State } from "../state";
import { factsForRevision } from "./factsForRevision";

export function parameterTarget(proposal: ChangeProposed, nodeId: string): string {
  return `parameter-calls-${encodeURIComponent(JSON.stringify([proposal.change_id, proposal.revision, proposal.head_commit, nodeId]))}`;
}

/** Optional evidence comes only from the latest whole payload for this candidate. */
export function parameterExtensions(state: State, changeId: string) {
  const proposal = state.changes[changeId]?.proposal;
  return proposal
    ? (factsForRevision(state, proposal)?.parameter_extensions ?? []).map(
        (entry: ParameterExtensionDiff) => ({
          ...entry,
          target: parameterTarget(proposal, entry.node_id),
        }),
      )
    : [];
}

/** Called only by an explicit popover action; a closed or stale review has no target. */
export function visitParameterEvidence(target: string) {
  const section = document.getElementById(target);
  if (!section?.closest('.review-body [aria-label="New-parameter calls"]')) return;
  section.scrollIntoView({ block: "start", behavior: "instant" });
  section.focus({ preventScroll: true });
}
