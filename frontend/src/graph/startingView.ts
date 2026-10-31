import type { Box, Camera } from "./camera";
import { FIT_PADDING } from "./geometry";

const EPSILON = 1e-7;

/** Candidate origins nearest the focus, bounded by the graph and keeping the focus visible. */
function axis(
  from: number,
  to: number,
  focusFrom: number,
  focusTo: number,
  room: number,
  padding: number,
) {
  const lo = from - padding;
  const hi = to + padding - room;
  const preferred =
    lo > hi ? (lo + hi) / 2 : Math.max(lo, Math.min(hi, (focusFrom + focusTo - room) / 2));
  return { lo, hi, preferred };
}

/** A box is either wholly visible or wholly outside, never split by a viewport edge. */
function uncut(
  frame: Box,
  x: number,
  y: number,
  width: number,
  height: number,
  margins: Box,
): boolean {
  return (
    frame.x2 <= x - margins.x1 + EPSILON ||
    frame.x1 >= x + width + margins.x2 - EPSILON ||
    frame.y2 <= y - margins.y1 + EPSILON ||
    frame.y1 >= y + height + margins.y2 - EPSILON ||
    (frame.x1 >= x - EPSILON &&
      frame.x2 <= x + width + EPSILON &&
      frame.y1 >= y - EPSILON &&
      frame.y2 <= y + height + EPSILON)
  );
}

function candidates(
  frames: readonly Box[],
  focus: Box,
  margins: Box,
  padding: number,
  range: ReturnType<typeof axis>,
  start: "x1" | "y1",
  end: "x2" | "y2",
  room: number,
  bounded: boolean,
) {
  if (range.lo > range.hi) return [range.preferred];
  const lo = Math.max(focus[end] - room, bounded ? range.lo : -Infinity);
  const hi = Math.min(focus[start], bounded ? range.hi : Infinity);
  if (lo > hi) return [range.preferred];
  const clamp = (value: number) => Math.max(lo, Math.min(hi, value));
  return [
    ...new Set([
      clamp(range.preferred),
      ...frames.flatMap((frame) =>
        [0, padding].flatMap((clearance) => [
          clamp(frame[start] - clearance),
          clamp(frame[end] + margins[start] + clearance),
          clamp(frame[start] - room - margins[end] - clearance),
          clamp(frame[end] - room + clearance),
        ]),
      ),
    ]),
  ].sort((a, b) => a - b);
}

/**
 * Place the focus using boxes already scaled to the caller's zoom, initially clamped inside the
 * graph's bounds. Sweep gutters on both axes and take the nearest camera that leaves every frame
 * whole. `area` excludes overlays in screen pixels. A graph smaller than the view is centered on
 * that axis. If no uncut view can contain the focus, retain the bounded camera; navigation remains
 * the person's choice.
 */
export function startingView(
  graph: Box,
  focus: Box,
  size: { width: number; height: number },
  padding = FIT_PADDING,
  frames: readonly Box[] = [],
  area: Box = { x1: 0, y1: 0, x2: size.width, y2: size.height },
  bounded = false,
): Camera {
  const width = area.x2 - area.x1;
  const height = area.y2 - area.y1;
  const margins = { x1: area.x1, y1: area.y1, x2: size.width - area.x2, y2: size.height - area.y2 };
  const horizontal = axis(graph.x1, graph.x2, focus.x1, focus.x2, width, padding);
  const vertical = axis(graph.y1, graph.y2, focus.y1, focus.y2, height, padding);
  let origin = { x: horizontal.preferred, y: vertical.preferred };
  let best = Infinity;
  const xs = candidates(frames, focus, margins, padding, horizontal, "x1", "x2", width, bounded);
  const ys = candidates(frames, focus, margins, padding, vertical, "y1", "y2", height, bounded);
  for (const x of xs) {
    for (const y of ys) {
      const distance = (x - horizontal.preferred) ** 2 + (y - vertical.preferred) ** 2;
      if (distance < best && frames.every((frame) => uncut(frame, x, y, width, height, margins))) {
        best = distance;
        origin = { x, y };
      }
    }
  }
  return { zoom: 1, pan: { x: area.x1 - origin.x, y: area.y1 - origin.y } };
}
