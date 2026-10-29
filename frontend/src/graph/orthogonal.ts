import { RouteQueue } from "./routeQueue";

/** Rectangle obstacles and orthogonal points in model coordinates. */
export interface Rectangle {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}
export interface XY {
  x: number;
  y: number;
}

export function crosses(a: XY, b: XY, box: Rectangle): boolean {
  return a.x === b.x
    ? box.x1 < a.x && a.x < box.x2 && Math.max(a.y, b.y) > box.y1 && Math.min(a.y, b.y) < box.y2
    : box.y1 < a.y && a.y < box.y2 && Math.max(a.x, b.x) > box.x1 && Math.min(a.x, b.x) < box.x2;
}

/** Reuse the boxes pierced by each row/column for all edges in a single routing pass. */
export function obstacleChecks<T extends Rectangle>(boxes: readonly T[]) {
  const columns = new Map<number, T[]>();
  const rows = new Map<number, T[]>();
  return (a: XY, b: XY, except?: (box: T) => boolean): boolean => {
    const vertical = a.x === b.x;
    const cache = vertical ? columns : rows;
    const coordinate = vertical ? a.x : a.y;
    let candidates = cache.get(coordinate);
    if (!candidates) {
      candidates = boxes.filter((box) =>
        vertical
          ? box.x1 < coordinate && coordinate < box.x2
          : box.y1 < coordinate && coordinate < box.y2,
      );
      cache.set(coordinate, candidates);
    }
    return candidates.some((box) => !except?.(box) && crosses(a, b, box));
  };
}

/** Remove collinear bends; repeated endpoints must not become zero-length renderer segments. */
export function simplify(points: readonly XY[]): XY[] {
  const result: XY[] = [];
  for (const point of points) {
    const previous = result.at(-1);
    if (previous?.x === point.x && previous.y === point.y) continue;
    const before = result.at(-2);
    if (
      before &&
      previous &&
      ((before.x === previous.x && previous.x === point.x) ||
        (before.y === previous.y && previous.y === point.y))
    )
      result.pop();
    result.push(point);
  }
  return result;
}

/** Visibility-grid shortest path outside every frame; ties prefer fewer bends, then grid order. */
export function orthogonalPath(
  start: XY,
  end: XY,
  boxes: readonly Rectangle[],
  clearance: number,
  blocked = obstacleChecks(boxes),
): XY[] | null {
  const clear = (a: XY, b: XY) => !blocked(a, b);
  const corner = { x: end.x, y: start.y };
  if (clear(start, corner) && clear(corner, end)) return simplify([start, corner, end]);
  const xs = [
    ...new Set([start.x, end.x, ...boxes.flatMap((b) => [b.x1 - clearance, b.x2 + clearance])]),
  ].sort((a, b) => a - b);
  const ys = [
    ...new Set([start.y, end.y, ...boxes.flatMap((b) => [b.y1 - clearance, b.y2 + clearance])]),
  ].sort((a, b) => a - b);
  return searchGrid(start, end, xs, ys, clearance, blocked);
}

/** Dijkstra on the same grid with the same cost/key tie order as the original sorted queue. */
function searchGrid(
  start: XY,
  end: XY,
  xs: number[],
  ys: number[],
  clearance: number,
  blocked: (a: XY, b: XY) => boolean,
): XY[] | null {
  const index = (p: XY) => ys.indexOf(p.y) * xs.length + xs.indexOf(p.x);
  const point = (id: number): XY => ({
    x: xs[id % xs.length] ?? 0,
    y: ys[Math.floor(id / xs.length)] ?? 0,
  });
  const first = index(start) * 2;
  const last = index(end);
  const costs = new Float64Array(xs.length * ys.length * 2).fill(Infinity);
  costs[first] = 0;
  const previous = new Int32Array(costs.length).fill(-1);
  const visibility = new Uint8Array(costs.length);
  const pending = new RouteQueue();
  pending.push({ key: first, cost: 0 });
  while (pending.length) {
    const current = pending.pop();
    if (!current || current.cost !== costs[current.key]) continue;
    const id = Math.floor(current.key / 2);
    if (id === last) {
      const path = [end];
      let key = current.key;
      while ((previous[key] ?? -1) >= 0) {
        key = previous[key] ?? first;
        path.push(point(Math.floor(key / 2)));
      }
      return simplify(path.reverse());
    }
    const a = point(id);
    const col = id % xs.length;
    const row = Math.floor(id / xs.length);
    const neighbors = [
      col > 0 ? id - 1 : -1,
      col < xs.length - 1 ? id + 1 : -1,
      row > 0 ? id - xs.length : -1,
      row < ys.length - 1 ? id + xs.length : -1,
    ];
    for (const next of neighbors) {
      if (next < 0) continue;
      const b = point(next);
      const direction = a.x === b.x ? 1 : 0;
      // Each undirected grid segment is visited from both ends and both arrival directions.
      const segment = Math.min(id, next) * 2 + direction;
      if (visibility[segment] === 2) continue;
      if (!visibility[segment]) {
        visibility[segment] = blocked(a, b) ? 2 : 1;
        if (visibility[segment] === 2) continue;
      }
      const key = next * 2 + direction;
      const cost =
        current.cost +
        Math.abs(b.x - a.x) +
        Math.abs(b.y - a.y) +
        (direction === current.key % 2 ? 0 : clearance);
      if (cost >= (costs[key] ?? Infinity)) continue;
      costs[key] = cost;
      previous[key] = current.key;
      pending.push({ key, cost });
    }
  }
  // Overlapping dragged frames can enclose an endpoint: there is no honest clear route.
  return null;
}
