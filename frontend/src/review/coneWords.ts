import type { ChangeFacts } from "@contracts";
import type { State } from "../state";
import { factsForRevision } from "./factsForRevision";
import { selectMeasuredCallers } from "./measuredCallers";

export type ConeWord = "differs" | "same" | "varied" | "not measured";

/** Admit a comparison only from the current revision's published counts. */
export function hasCompared(facts: ChangeFacts | undefined) {
  return [...(facts?.behavior_diff ?? []), ...(facts?.measured_cone ?? [])].some(
    (entry) => entry.compared_count > 0,
  );
}

/** Static callers without admitted evidence never acquire an unchanged-result claim. */
export function selectConeWords(state: State, changeId: string) {
  const proposal = state.changes[changeId]?.proposal;
  if (!proposal || !hasCompared(factsForRevision(state, proposal))) return null;
  return new Map<string, ConeWord | null>(
    (selectMeasuredCallers(state, changeId)?.rows ?? []).map(({ nodeId, evidence: e }) => [
      nodeId,
      !e || !e.compared_count || e.not_probed
        ? "not measured"
        : e.rows.some((row) => row.unstable)
          ? "varied"
          : e.outcome === "changed" && e.differing_count > 0
            ? "differs"
            : e.complete && e.outcome === "unchanged" && e.differing_count === 0
              ? "same"
              : null,
    ]),
  );
}
