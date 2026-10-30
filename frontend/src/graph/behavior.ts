// eslint-disable-next-line no-restricted-imports -- B2n reuses line A's selector; review exports are outside this front.
import { selectBehaviorEvidence, type BehaviorNodeView } from "../review/behaviorEvidence";
import type { State } from "../state";

export interface GraphBehaviorEntry {
  nodeId: string;
  changeId: string;
  agentId: string;
  status: BehaviorNodeView["status"];
  compared: number;
  differing: number;
  unstable: boolean;
  noDifference: boolean;
  limitation: BehaviorNodeView["limitation"];
}

/** Current open proposals only, ordered by change id then published node order. */
export function selectGraphBehavior(state: State): GraphBehaviorEntry[] {
  return Object.keys(state.changes)
    .sort()
    .flatMap((changeId) => {
      const change = state.changes[changeId];
      if (!change || change.status !== "ready_for_review" || change.resolution !== null) return [];
      return (selectBehaviorEvidence(state, changeId) ?? []).map((node) => ({
        nodeId: node.node_id,
        changeId,
        agentId: change.agent_id,
        status: node.status,
        compared: node.compared_count,
        differing: node.differing_count,
        unstable: node.rows.some((row) => row.unstable),
        noDifference: node.noDifference,
        limitation: node.limitation,
      }));
    });
}
