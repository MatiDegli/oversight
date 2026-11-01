import type { RevertChange } from "@contracts";
import type { State } from "../state";
import { selectChangeCard } from "./inboxSelectors";

export interface RevertAttempt {
  run: State["run"];
  target: RevertChange;
  afterSeq: number;
  phase: "pending" | "refused";
  message?: string;
}
export type RevertView =
  | { kind: "none" }
  | ({ body: RevertChange } & (
      | { kind: "ready" | "pending" }
      | { kind: "refused"; message: string }
      | { kind: "reverted"; revert_commit: string }
      | { kind: "needs_you"; reason: string }
    ));

/** Recorded evidence determines outcomes; an attempt only describes the person's last click. */
export function revertView(state: State, changeId: string, last?: RevertAttempt): RevertView {
  const change = state.changes[changeId];
  const merge_commit = change?.resolution?.merge_commit;
  const resolution_seq = change?.resolutionEvent?.seq;
  const main_commit = state.graph?.commit;
  if (
    selectChangeCard(state, changeId)?.kind !== "merged" ||
    !merge_commit ||
    resolution_seq === undefined ||
    !main_commit
  )
    return { kind: "none" };
  const body = { resolution_seq, merge_commit, main_commit };
  const attempt =
    last?.run === state.run &&
    last.target.resolution_seq === resolution_seq &&
    last.target.merge_commit === merge_commit
      ? last
      : undefined;
  const outcome = state.decisionEvents.findLast(
    (e) =>
      e.type === "revert.resolved" &&
      e.payload.change_id === changeId &&
      e.payload.resolution_seq === resolution_seq &&
      e.payload.merge_commit === merge_commit,
  );
  if (attempt && (!outcome || outcome.seq <= attempt.afterSeq)) {
    const error = state.decisionEvents.findLast(
      (e) =>
        e.seq > attempt.afterSeq &&
        e.type === "error" &&
        e.payload.message.startsWith(`Revert of ${changeId} did not run`),
    );
    if (error?.type === "error") return { kind: "refused", body, message: error.payload.message };
    if (attempt.phase === "refused")
      return { kind: "refused", body, message: attempt.message ?? "" };
    return { kind: "pending", body };
  }
  if (outcome?.type === "revert.resolved") {
    const result = outcome.payload;
    return result.result === "reverted"
      ? { kind: "reverted", body, revert_commit: result.revert_commit ?? "" }
      : { kind: "needs_you", body, reason: result.reason ?? "" };
  }
  return { kind: "ready", body };
}
