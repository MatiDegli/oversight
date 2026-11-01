import type { ServerEvent } from "@contracts";
import { applyEvent, createInitialState } from "../state";

/** Milliseconds on the fixed 1× axis, independent of the selected rate. */
export function replayOffsets(events: readonly ServerEvent[]): number[] {
  let offset = 0;
  return events.map((event, index) => {
    const previous = events[index - 1];
    if (previous)
      offset += Math.min(Math.max(Date.parse(event.ts) - Date.parse(previous.ts), 0), 2000);
    return offset;
  });
}

export function replayPrefix(
  events: readonly ServerEvent[],
  offsets: readonly number[],
  position: number,
) {
  return events.reduce((state, event, index) => {
    if ((offsets[index] ?? Infinity) > position) return state;
    const next = applyEvent(state, event);
    if (next.gap)
      throw new Error(`Replay sequence or graph version mismatch at ${String(event.seq)}`);
    return next;
  }, createInitialState());
}
