import assert from "node:assert/strict";

interface Box {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export const bounds = (nodes: readonly Box[]): Box => ({
  x1: Math.min(...nodes.map((node) => node.x1)),
  y1: Math.min(...nodes.map((node) => node.y1)),
  x2: Math.max(...nodes.map((node) => node.x2)),
  y2: Math.max(...nodes.map((node) => node.y2)),
});

function axisPans(
  graph: [number, number],
  target: [number, number],
  area: [number, number],
  size: number,
  modules: [number, number][],
) {
  const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));
  const graphMin = area[1] - graph[1] - 40;
  const graphMax = area[0] - graph[0] + 40;
  const centered = (area[0] + area[1] - target[0] - target[1]) / 2;
  const preferred =
    graphMin > graphMax ? (graphMin + graphMax) / 2 : clamp(centered, graphMin, graphMax);
  const lo = Math.max(graphMin, area[0] - target[0]);
  const hi = Math.min(graphMax, area[1] - target[1]);
  const candidates =
    lo > hi
      ? [preferred]
      : [
          clamp(preferred, lo, hi),
          ...modules.flatMap(([start, end]) =>
            [0, 40].flatMap((gap) =>
              [area[0] - start + gap, -end - gap, size - start + gap, area[1] - end - gap].map(
                (pan) => clamp(pan, lo, hi),
              ),
            ),
          ),
        ];
  return {
    preferred,
    fallback: clamp(preferred, area[0] - target[0], area[1] - target[1]),
    candidates: [...new Set(candidates)],
  };
}

/** Independent screen-space oracle: prefer the nearest uncut view to the bounded center. */
export function assertCenteredFrame(
  frame: {
    zoom: number;
    pan: { x: number; y: number };
    size: { width: number; height: number };
    graph: readonly Box[];
    modules: readonly Box[];
  },
  targets: readonly Box[],
  area: Box,
): void {
  const scale = (box: Box): Box => ({
    x1: box.x1 * frame.zoom,
    y1: box.y1 * frame.zoom,
    x2: box.x2 * frame.zoom,
    y2: box.y2 * frame.zoom,
  });
  const graph = scale(bounds(frame.graph));
  const target = scale(bounds(targets));
  const modules = frame.modules.map(scale);
  const x = axisPans(
    [graph.x1, graph.x2],
    [target.x1, target.x2],
    [area.x1, area.x2],
    frame.size.width,
    modules.map((box) => [box.x1, box.x2]),
  );
  const y = axisPans(
    [graph.y1, graph.y2],
    [target.y1, target.y2],
    [area.y1, area.y2],
    frame.size.height,
    modules.map((box) => [box.y1, box.y2]),
  );
  const uncut = (pan: { x: number; y: number }) =>
    modules.every(
      (box) =>
        box.x2 + pan.x <= 1e-6 ||
        box.x1 + pan.x >= frame.size.width - 1e-6 ||
        box.y2 + pan.y <= 1e-6 ||
        box.y1 + pan.y >= frame.size.height - 1e-6 ||
        (box.x1 + pan.x >= area.x1 - 1e-6 &&
          box.x2 + pan.x <= area.x2 + 1e-6 &&
          box.y1 + pan.y >= area.y1 - 1e-6 &&
          box.y2 + pan.y <= area.y2 + 1e-6),
    );
  const offset = (pan: { x: number; y: number }) =>
    (pan.x - x.preferred) ** 2 + (pan.y - y.preferred) ** 2;
  const candidates = x.candidates
    .flatMap((px) => y.candidates.map((py) => ({ x: px, y: py })))
    .filter(uncut);
  if (candidates.length) {
    assert(uncut(frame.pan), "prefer an uncut module frame when one fits");
    assert(
      Math.abs(offset(frame.pan) - Math.min(...candidates.map(offset))) < 1e-4,
      "camera must be nearest to the center within graph bounds",
    );
  } else {
    assert(
      Math.abs(frame.pan.x - x.fallback) < 1e-6 && Math.abs(frame.pan.y - y.fallback) < 1e-6,
      "camera must center targets within graph bounds when no uncut view fits",
    );
  }
}
