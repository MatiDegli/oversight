import type { ImpactCone } from "@contracts";
import { selectSignalLights } from "../graph";
import type { FieldInput, FieldScope } from "../graph";
import type { State } from "../state";
import { litNodeIds } from "./view";
import type { View } from "./view";

/**
 * The field's presentation mode follows the open panel; a decided change returns to watch,
 * as `litNodeIds` does for graph dimming. R4 emitters are independent of this navigation.
 */
export function fieldScope(state: State, view: View): FieldScope {
  const changeId = view.review ?? view.diff?.changeId ?? null;
  if (changeId !== null) {
    const waiting = litNodeIds(state.changes[changeId]) !== null;
    if (waiting)
      return view.review !== null ? { mode: "review", changeId } : { mode: "live-diff", changeId };
    return { mode: "watch" };
  }
  if (view.file) return { mode: "file", nodeId: view.selected };
  return { mode: "watch" };
}

/** The mode and lights; the graph's optional cone argument never supplies emitters. */
export const fieldInput: (state: State, view: View, liveCone?: ImpactCone | null) => FieldInput = (
  state,
  view,
) => ({
  mode: fieldScope(state, view).mode,
  lights: selectSignalLights(state, state.lastReads),
});
