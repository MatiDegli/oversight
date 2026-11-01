import type { State } from "./types";

/** Events supersede run metadata; recordings without either keep the mode unknown. */
export function selectMergeMode(state: State) {
  return state.mergeMode ?? state.run?.merge_mode ?? null;
}
