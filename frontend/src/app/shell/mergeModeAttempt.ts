import { selectMergeMode, type State } from "../../state";

/** Kept by App so leaving the tab neither loses a refusal nor admits another request. */
export type MergeModeAttempt = {
  run: State["run"];
  revision: number;
} & ({ phase: "pending"; responseRevision?: number } | { phase: "failed"; message: string });

export function mergeModePending(state: State, attempt?: MergeModeAttempt): boolean {
  return (
    attempt?.run === state.run &&
    attempt.phase === "pending" &&
    (attempt.responseRevision === undefined ||
      (selectMergeMode(state)?.revision ?? -1) < attempt.responseRevision)
  );
}
