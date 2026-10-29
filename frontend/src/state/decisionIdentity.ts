import type { BehaviorDiffRow, DecisionRef, Slot, TaskExample } from "@contracts";

/** Revert has no DecisionRef variant: expose its recorded identity without inventing a link. */
export interface RevertInboxRef {
  kind: "revert_needs_you";
  change_id: string;
  revision: number;
  resolution_seq: number;
  merge_commit: string;
}

export type InboxIdentity = DecisionRef | RevertInboxRef;
export type InboxExample =
  | { source: "review_card"; value: BehaviorDiffRow }
  | { source: "task"; value: TaskExample }
  | { source: "none"; message: "No recorded example" };

export interface DecisionInboxEntry {
  id: string;
  kind: InboxIdentity["kind"];
  identity: InboxIdentity;
  changeId: string | null;
  agentId: string | null;
  slot: Slot | null;
  planId: string | null;
  reason: string;
  example: InboxExample;
  firstSeq: number;
  ts: string;
  age: string;
}

/** Evidence guards a reference, but does not split repeated observations of the same entry. */
export function decisionIdentityKey(ref: InboxIdentity): string {
  if (ref.kind === "lead_plan") return JSON.stringify([ref.kind, ref.agent_id, ref.revision]);
  if (ref.kind === "revert_needs_you") {
    return JSON.stringify([
      ref.kind,
      ref.change_id,
      ref.revision,
      ref.resolution_seq,
      ref.merge_commit,
    ]);
  }
  return JSON.stringify([
    ref.kind,
    ref.change_id,
    ref.revision,
    ref.head_commit,
    ...(ref.kind === "review_card" ? [ref.node_id, ref.question] : []),
  ]);
}

export function matchesDecision(ref: DecisionRef, identity: InboxIdentity): boolean {
  return (
    identity.kind !== "revert_needs_you" &&
    decisionIdentityKey(ref) === decisionIdentityKey(identity) &&
    ref.evidence_seq === identity.evidence_seq
  );
}

export const noRecordedExample: InboxExample = { source: "none", message: "No recorded example" };
