import type { Box, Camera, FrameNode } from "./camera";
import { getActiveTheme } from "../theme";
import { FIT_PADDING } from "./geometry";
import { startingView } from "./startingView";

/** Both the name and its state word (including “changed”) must remain at least 11 CSS px. */
export function changeZoom(
  theme: {
    "node-name-size": string;
    "node-word-size": string;
  } = getActiveTheme(),
) {
  return {
    min:
      11 /
      Math.min(
        Number.parseFloat(theme["node-name-size"]),
        Number.parseFloat(theme["node-word-size"]),
      ),
    max: 1,
  };
}

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

/** Squared gap to the nearest changed box, independent of cone order and module membership. */
function proximity(node: Box, changes: readonly Box[]): number {
  return Math.min(
    ...changes.map(
      (change) =>
        Math.max(0, change.x1 - node.x2, node.x1 - change.x2) ** 2 +
        Math.max(0, change.y1 - node.y2, node.y1 - change.y2) ** 2,
    ),
  );
}

/** Whole cone, then distance 1, then the nearest distance 1 members that still fit. */
function targetsAtFloor(
  changes: readonly FrameNode[],
  callers: readonly FrameNode[],
  fits: (nodes: readonly FrameNode[]) => boolean,
): FrameNode[] {
  const whole = [...changes, ...callers];
  if (fits(whole)) return whole;
  const near = callers.filter((node) => node.distance === 1);
  const firstRing = [...changes, ...near];
  if (fits(firstRing)) return firstRing;
  const targets = [...changes];
  for (const node of near.sort(
    (a, b) => proximity(a, changes) - proximity(b, changes) || a.id.localeCompare(b.id),
  )) {
    if (fits([...targets, node])) targets.push(node);
  }
  return targets;
}

/** Keep labels legible and every selected box clear of overlays, without moving any node. */
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
  const callers = boxes(entries.map((entry) => entry.node_id)).map((node) => ({
    ...node,
    distance: Math.min(
      ...entries
        .filter((entry) => (representatives?.get(entry.node_id) ?? entry.node_id) === node.id)
        .map((entry) => entry.distance),
    ),
  }));
  if (!changes.length && !callers.length) return null;
  const limits = changeZoom();
  const fit = (box: Box) =>
    Math.min(width / Math.max(1, box.x2 - box.x1), height / Math.max(1, box.y2 - box.y1));
  const fits = (targets: readonly FrameNode[]) =>
    targets.length > 0 && fit(union(targets)) >= limits.min - 1e-7;
  const targets = targetsAtFloor(changes, callers, fits);
  if (!targets.length) return null;
  const target = union(targets);
  const partial = !fits([...changes, ...callers.filter((node) => node.distance === 1)]);
  const zoom = partial ? limits.min : Math.max(limits.min, Math.min(limits.max, fit(target)));
  const camera = startingView(
    scale(graph, zoom),
    scale(target, zoom),
    size,
    FIT_PADDING,
    frames.map((frame) => scale(frame, zoom)),
    area,
    true,
  );
  // Module gutters are a preference; the selected boxes staying outside overlays takes priority.
  const clamp = (value: number, lo: number, hi: number) =>
    lo <= hi ? Math.max(lo, Math.min(hi, value)) : value;
  return {
    zoom,
    pan: {
      x: clamp(camera.pan.x, area.x1 - target.x1 * zoom, area.x2 - target.x2 * zoom),
      y: clamp(camera.pan.y, area.y1 - target.y1 * zoom, area.y2 - target.y2 * zoom),
    },
  };
}
