import type { PreviewCandidate } from "./previewPort";

export interface OpeningConnection {
  run: string;
  source: string;
  generation: number;
}
export type PreviewSurface =
  | "chat-wide"
  | "chat-narrow"
  | "oversight-watch"
  | "oversight-focus"
  | "usage"
  | "settings"
  | "inbox";
export interface OpeningGuards {
  surface: PreviewSurface;
  configured: boolean;
  preference: boolean;
  targetVisible: boolean;
  allocatedSlot: boolean;
  openReview: boolean;
  openPreview: boolean;
  layoutUnchanged: boolean;
  tabUnchanged: boolean;
  framingUnchanged: boolean;
  focusUnchanged: boolean;
  scrollUnchanged: boolean;
}
export interface OpeningState {
  connection: OpeningConnection | null;
  accepted: number;
  fence: number | null;
  armed: boolean;
  seen: readonly string[];
}
export type OpeningInput =
  | { type: "reset"; connection: OpeningConnection | null }
  | { type: "gap" }
  | { type: "fence"; connection: OpeningConnection; seq: number; committed: boolean }
  | { type: "prefix"; connection: OpeningConnection; seq: number; contiguous: boolean }
  | {
      type: "finish";
      connection: OpeningConnection;
      seq: number;
      live: boolean;
      genuinelyNew: boolean;
      candidate: PreviewCandidate;
      guards: OpeningGuards;
    };
export const initialOpening: OpeningState = {
  connection: null,
  accepted: 0,
  fence: null,
  armed: false,
  seen: [],
};
const same = (a: OpeningConnection | null, b: OpeningConnection) =>
  a?.run === b.run && a.source === b.source && a.generation === b.generation;
const validSeq = (seq: number) => Number.isSafeInteger(seq) && seq >= 0;
function permitted(guards: OpeningGuards) {
  return (
    guards.configured &&
    guards.preference &&
    guards.targetVisible &&
    guards.allocatedSlot &&
    !guards.openReview &&
    !guards.openPreview &&
    guards.layoutUnchanged &&
    guards.tabUnchanged &&
    guards.framingUnchanged &&
    guards.focusUnchanged &&
    guards.scrollUnchanged
  );
}
/** Pure and unwired. onopen/Transport.ready are deliberately not inputs or arming signals.
 * There is no established production slot; this function promises no production auto-open.
 * Suppressed finishes are consumed, never queued for replay when a guard later changes.
 */
export function previewOpening(
  state: OpeningState,
  input: OpeningInput,
): {
  state: OpeningState;
  action: "see_preview" | "open_preview" | null;
} {
  if (input.type === "reset")
    return {
      state: { ...initialOpening, connection: input.connection, seen: state.seen },
      action: null,
    };
  if (input.type === "gap")
    return { state: { ...state, connection: null, armed: false, fence: null }, action: null };
  if (!same(state.connection, input.connection)) return { state, action: null };
  if (input.type === "fence") {
    if (!input.committed || !validSeq(input.seq) || state.fence !== null)
      return { state, action: null };
    return {
      state: { ...state, fence: input.seq, armed: state.accepted >= input.seq },
      action: null,
    };
  }
  if (input.type === "prefix") {
    if (!input.contiguous || !validSeq(input.seq) || input.seq < state.accepted)
      return { state: { ...state, connection: null, armed: false, fence: null }, action: null };
    return {
      state: {
        ...state,
        accepted: input.seq,
        armed: state.fence !== null && input.seq >= state.fence,
      },
      action: null,
    };
  }
  const key = JSON.stringify([
    input.candidate.run_id,
    input.candidate.agent_id,
    input.candidate.revision,
    input.candidate.head_commit,
  ]);
  if (state.seen.includes(key)) return { state, action: null };
  const consumed = { ...state, seen: [...state.seen, key] };
  if (
    !state.armed ||
    state.fence === null ||
    !validSeq(input.seq) ||
    input.seq <= state.fence ||
    input.seq > state.accepted ||
    !input.live ||
    !input.genuinelyNew ||
    input.candidate.run_id !== input.connection.run
  )
    return { state: consumed, action: null };
  return { state: consumed, action: permitted(input.guards) ? "open_preview" : "see_preview" };
}
