import type { MeasuredCaller } from "@contracts";
import type { State } from "../state";
import { factsForRevision } from "./currentFacts";

export const callerOutcomes = {
  changed: "Result change measured",
  unchanged: "No difference measured on these inputs",
  not_compared: "Possible, no change measured",
} as const;

/** Availability describes admitted invocations, independently of the published outcome. */
export function callerAvailability(caller: MeasuredCaller) {
  return caller.compared_count === 0
    ? "Measurements unavailable"
    : caller.complete
      ? "Comparisons available"
      : "Partial measurements";
}

/** Only the current proposal's retained static entries participate; edges prove no measurement. */
export function selectMeasuredCallers(state: State, changeId: string) {
  const change = state.changes[changeId];
  const proposal = change?.proposal;
  if (!proposal) return null;
  const evidence = factsForRevision(state, proposal)?.measured_cone;
  const recording = evidence == null ? "absent" : evidence.length === 0 ? "empty" : "recorded";
  const byId = new Map(evidence?.map((caller) => [caller.node_id, caller]));
  const names = new Map(
    [...(state.graph?.nodes ?? []), ...proposal.graph.nodes_added].map((node) => [
      node.id,
      node.qualname,
    ]),
  );
  const rows = proposal.impact.entries.map((entry) => {
    const caller = byId.get(entry.node_id);
    return {
      nodeId: entry.node_id,
      name: names.get(entry.node_id) ?? entry.node_id,
      distance: entry.distance,
      label: callerOutcomes[caller?.outcome ?? "not_compared"],
      availability: caller ? callerAvailability(caller) : null,
      evidence: caller ?? null,
    };
  });
  return {
    recording,
    rows,
    maxDepth: proposal.impact.max_depth,
    maxNodes: proposal.impact.max_nodes,
  };
}
