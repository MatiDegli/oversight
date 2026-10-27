import type { ImpactCone } from "@contracts";
import { liveImpact } from "../graph";
import type { State } from "../state";
import { changeNodeIds } from "./view";
import type { View } from "./view";

/** One cone for graph dimming, distance badges, the card and the field's live work-light. */
export function graphScope(
  state: State,
  view: View,
): { cone: ImpactCone | null; litIds: string[] | null } {
  const id = view.review ?? view.diff?.changeId;
  const change = id === undefined ? undefined : state.changes[id];
  if (!change || change.status === "approved" || change.status === "rejected")
    return { cone: null, litIds: null };
  const cone = view.review === null ? liveImpact(state, change) : (change.proposal?.impact ?? null);
  const focus = state.focus[change.agent_id];
  const reads = view.review === null && focus?.mode === "read" ? focus.node_ids : [];
  return {
    cone,
    litIds: [
      ...new Set([
        ...changeNodeIds(change),
        ...(cone?.entries.map((entry) => entry.node_id) ?? []),
        ...reads,
      ]),
    ],
  };
}
