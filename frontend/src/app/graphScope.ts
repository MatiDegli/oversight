import type { ImpactCone } from "@contracts";
import { liveImpact } from "../graph";
import type { State } from "../state";
import { changeNodeIds } from "./view";
import type { View } from "./view";

/** Keep review's card/camera scope separate from the live cone's dimming and work-light. */
export function graphScope(
  state: State,
  view: View,
): { cone: ImpactCone | null; liveCone: ImpactCone | null; litIds: string[] | null } {
  const id = view.review ?? view.diff?.changeId;
  const change = id === undefined ? undefined : state.changes[id];
  if (!change || change.status === "approved" || change.status === "rejected")
    return { cone: null, liveCone: null, litIds: null };
  const cone = view.review === null ? null : (change.proposal?.impact ?? null);
  const liveCone = view.review === null ? liveImpact(state, change) : null;
  const focus = state.focus[change.agent_id];
  const reads = view.review === null && focus?.mode === "read" ? focus.node_ids : [];
  return {
    cone,
    liveCone,
    litIds: [
      ...new Set([
        ...changeNodeIds(change),
        ...((cone ?? liveCone)?.entries.map((entry) => entry.node_id) ?? []),
        ...reads,
      ]),
    ],
  };
}
