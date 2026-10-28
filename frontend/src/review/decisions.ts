import type {
  ApproveChange,
  RejectChange,
  RequestChanges,
  ChangeResolved,
  ErrorReport,
} from "@contracts";

import type { ChangeState, State } from "../state";
import { commandFailure } from "../transport";

export type DecisionAction = "approve" | "reject" | "request-changes";

/**
 * What the person's last click on a change started. The app keeps it per change, so closing and
 * reopening review does not enable the buttons while the server still works on the decision. It
 * belongs to the revision that was proposed at the click: a new proposal starts with no attempt.
 */
export type DecisionAttempt =
  | {
      phase: "pending";
      action: DecisionAction;
      /** The proposal's revision at the click (0 before any). */
      revision: number;
      /** The change's resolution at the click: a different one is the server's answer. */
      resolution: ChangeResolved | null;
      /** How many errors the state held at the click: a later one naming the change ends it. */
      errorCount: number;
    }
  | { phase: "failed"; action: DecisionAction; revision: number; message: string };

/** The revision on the table: the latest proposal's, or 0 while the change was never proposed. */
export const revisionOf = (change: ChangeState | undefined): number =>
  change?.proposal?.revision ?? 0;

/** What the decision area shows. */
export type DecisionView =
  /** A recorded run: no commands, and the recorded decision once it arrives. */
  | { kind: "recorded"; resolution: ChangeResolved | null }
  /** All three decisions available, with the last attempt's failure, if any. */
  | { kind: "open"; failure: string | null }
  /** From the click until change.resolved, a 4xx, or an error naming the change. */
  | { kind: "pending"; action: DecisionAction }
  | { kind: "revising"; resolution: ChangeResolved | null }
  /** Approved but not merged: the person may request changes or reject it. */
  | { kind: "conflict"; resolution: ChangeResolved; failure: string | null }
  /** Approved and merged, or rejected: nothing is left to decide. */
  | { kind: "decided"; resolution: ChangeResolved };

export function startAttempt(state: State, changeId: string, action: DecisionAction) {
  return {
    phase: "pending",
    action,
    revision: revisionOf(state.changes[changeId]),
    resolution: state.changes[changeId]?.resolution ?? null,
    errorCount: state.errors.length,
  } satisfies DecisionAttempt;
}

const verbs: Record<DecisionAction, string> = {
  approve: "Approve",
  reject: "Reject",
  "request-changes": "Request changes",
};

/** A 4xx shows the server's message; a 500 or a lost request shows a plain failure line. */
export function failureMessage(action: DecisionAction, error: unknown): string {
  return commandFailure(verbs[action], error);
}

export function decisionView(
  change: ChangeState,
  errors: readonly ErrorReport[],
  last: DecisionAttempt | undefined,
  recorded: boolean,
): DecisionView {
  const { resolution } = change;
  if (recorded) return { kind: "recorded", resolution };
  // An attempt on an earlier revision says nothing about this one, whose resolution is null again.
  const attempt = last?.revision === revisionOf(change) ? last : undefined;
  let failure = attempt?.phase === "failed" ? attempt.message : null;
  if (attempt?.phase === "pending" && resolution === attempt.resolution) {
    const named = errors
      .slice(attempt.errorCount)
      .find((error) => error.change_id === change.change_id);
    if (!named) return { kind: "pending", action: attempt.action };
    failure = named.message;
  }
  if (resolution && (change.status === "approved" || change.status === "rejected")) {
    return { kind: "decided", resolution };
  }
  if (resolution && change.status === "conflict") return { kind: "conflict", resolution, failure };
  if (change.status === "revising" || change.status === "editing")
    return { kind: "revising", resolution };
  return { kind: "open", failure };
}

export interface DecisionClient {
  approveChange: (id: string, body: ApproveChange) => Promise<unknown>;
  requestChanges: (id: string, body: RequestChanges) => Promise<unknown>;
  rejectChange: (id: string, body: RejectChange) => Promise<unknown>;
}

/**
 * Sends one decision. The attempt turns pending before the request leaves, so the buttons disable
 * on the click; 202 changes nothing more (the answer comes as events) and a failure records its
 * line. An empty comment is left out; the observed target is always sent.
 */
export async function sendDecision(
  client: DecisionClient,
  state: State,
  changeId: string,
  action: DecisionAction,
  comment: string,
  record: (attempt: DecisionAttempt) => void,
  target?: Pick<RejectChange, "revision" | "head_commit">,
): Promise<void> {
  if (action === "request-changes" && comment.trim() === "") return;
  const started = startAttempt(state, changeId, action);
  record(started);
  const text = comment.trim();
  const proposal = state.changes[changeId]?.proposal;
  const agentId = state.changes[changeId]?.agent_id;
  const base = agentId ? state.agents[agentId]?.base_commit : null;
  const head_commit = target?.head_commit ?? proposal?.head_commit ?? base;
  const revision = target?.revision ?? proposal?.revision ?? 0;
  if (!head_commit || (action !== "reject" && revision === 0)) {
    record({
      phase: "failed",
      action,
      revision,
      message: "Refresh and review the change before deciding.",
    });
    return;
  }
  const body = { revision, head_commit, ...(text === "" ? {} : { comment: text }) };
  try {
    await (action === "approve"
      ? client.approveChange(changeId, body)
      : action === "request-changes"
        ? client.requestChanges(changeId, { ...body, comment: text })
        : client.rejectChange(changeId, body));
  } catch (error) {
    record({
      phase: "failed",
      action,
      revision: started.revision,
      message: failureMessage(action, error),
    });
  }
}
