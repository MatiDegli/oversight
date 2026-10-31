import type { NodeBehaviorDiff } from "@contracts";

import type { State } from "../state";
import { factsForRevision } from "./factsForRevision";
import { evidenceWords } from "./evidenceWords";

export interface BehaviorNodeView extends NodeBehaviorDiff {
  label: string;
  status: "Available" | "Partial" | "Unavailable";
  limitation: string | null;
  words: string | null;
  noDifference: boolean;
}

export const behaviorNotRecorded = "Behavior evidence was not recorded for this revision.";

function limitation(reason: string | null, kind: string | undefined): string | null {
  switch (reason) {
    case null:
      return null;
    case "unsupported node kind":
      return kind === "method" || kind === "class"
        ? "Methods and classes are not probed yet."
        : "This node type is not probed yet.";
    case "version unavailable":
      return "The base or proposed version is unavailable.";
    case "probe unavailable":
      return "Behavior probing was unavailable; no further reason was published.";
    default:
      return reason;
  }
}

/** Direct changed-node probes only. Measured callers are separate evidence. */
export function selectBehaviorEvidence(state: State, changeId: string): BehaviorNodeView[] | null {
  const proposal = state.changes[changeId]?.proposal;
  if (!proposal) return null;
  const evidence = factsForRevision(state, proposal)?.behavior_diff;
  if (evidence == null) return null;
  const nodes = new Map(state.graph?.nodes.map((node) => [node.id, node]));
  return evidence.map((probe) => {
    const node = nodes.get(probe.node_id);
    const unstable = probe.rows.some((row) => row.unstable);
    const limited = probe.not_probed !== null || unstable;
    return {
      ...probe,
      label: node?.qualname ?? probe.node_id,
      status: probe.compared_count === 0 ? "Unavailable" : limited ? "Partial" : "Available",
      limitation: limitation(probe.not_probed, node?.kind),
      words: evidenceWords(probe.not_probed, node?.kind),
      noDifference: probe.compared_count > 0 && probe.differing_count === 0 && !limited,
    };
  });
}
