import type { Box, Camera, FrameNode } from "./camera";
import { FIT_PADDING } from "./geometry";
import { startingView } from "./startingView";

export const CHANGE_ZOOM = { min: 0.4, max: 1 } as const;

const union = (boxes: readonly Box[]): Box => ({
  x1: Math.min(...boxes.map((box) => box.x1)),
  y1: Math.min(...boxes.map((box) => box.y1)),
  x2: Math.max(...boxes.map((box) => box.x2)),
  y2: Math.max(...boxes.map((box) => box.y2)),
});
const scale = (box: Box, zoom: number): Box => ({
  x1: box.x1 * zoom,
  y1: box.y1 * zoom,
  x2: box.x2 * zoom,
  y2: box.y2 * zoom,
});

export interface ChangeFrameInput {
  nodes: readonly FrameNode[];
  changed: readonly string[];
  cone: readonly { node_id: string; distance: number }[];
  maxDepth: number;
  /** Folded descendants resolve to their visible class row, without changing any position. */
  representatives?: ReadonlyMap<string, string>;
  frames: readonly Box[];
  graph: Box;
  size: { width: number; height: number };
  /** Screen-space area clear of the cone card and other graph overlays. */
  area: Box;
}

/** Frame the complete bounded cone, or the change and first ring when it cannot fit at 40%. */
export function changeFrame(input: ChangeFrameInput): Camera | null {
  const { nodes, changed, cone, maxDepth, representatives, frames, graph, size, area } = input;
  const width = area.x2 - area.x1;
  const height = area.y2 - area.y1;
  if (width <= 0 || height <= 0) return null;
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const boxes = (ids: readonly string[]) =>
    [...new Set(ids.map((id) => representatives?.get(id) ?? id))].flatMap((id) => {
      const node = byId.get(id);
      return node ? [node] : [];
    });
  const changes = boxes(changed);
  const entries = cone.filter((entry) => entry.distance <= maxDepth);
  let targets = [...changes, ...boxes(entries.map((entry) => entry.node_id))];
  if (!targets.length) return null;
  const fit = (box: Box) =>
    Math.min(width / Math.max(1, box.x2 - box.x1), height / Math.max(1, box.y2 - box.y1));
  if (fit(union(targets)) < CHANGE_ZOOM.min) {
    targets = [...changes, ...boxes(entries.filter((e) => e.distance <= 1).map((e) => e.node_id))];
    if (!targets.length) return null;
  }
  const target = union(targets);
  const zoom = Math.max(CHANGE_ZOOM.min, Math.min(CHANGE_ZOOM.max, fit(target)));
  // If even the first ring is too wide, anchor the changes at 40%; visibility stays honest.
  const focus = fit(target) < CHANGE_ZOOM.min && changes.length ? union(changes) : target;
  const camera = startingView(
    scale(graph, zoom),
    scale(focus, zoom),
    size,
    FIT_PADDING,
    frames.map((frame) => scale(frame, zoom)),
    area,
    true,
  );
  return { ...camera, zoom };
}
