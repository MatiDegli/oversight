import { selectLights } from "../graph";
import type { FieldInput, FieldScope } from "../graph";
import type { State } from "../state";
import { litNodeIds } from "./view";
import type { View } from "./view";

/**
 * What the dot field lights for what the person has open: review and the live diff light their
 * change, the file view its node, and watch is the default. A change the person has decided is
 * resolved, and the graph goes back to its normal lights while the panel shows the outcome, as
 * `litNodeIds` does for the dimming.
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

/** The mode and the lights for the graph's dot field. */
export function fieldInput(state: State, view: View): FieldInput {
  const scope = fieldScope(state, view);
  return { mode: scope.mode, lights: selectLights(state, scope) };
}
