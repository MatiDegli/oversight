import type { Point } from "@contracts";
import type { GraphElement } from "./elements";
import type { GeometryTokens } from "./geometry";
import { nodeGeometry } from "./geometry";

export interface GroupBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Nine slots per column; open cards grow their slot without changing module order. */
export function groupPack(
  roots: readonly GraphElement[],
  full: ReadonlyMap<string, GroupBox>,
  heights: ReadonlyMap<string, number>,
  theme: GeometryTokens,
) {
  const g = nodeGeometry(theme);
  const cellWidth =
    Math.max(
      g.rowWidth + g.outer.module.left + g.outer.module.right,
      ...roots.map((r) => full.get(r.data.id)?.width ?? 0),
    ) + g.gutter;
  const cellHeight = g.headerHeight + 150;
  const buckets = new Map<string, GraphElement[]>();
  for (const root of roots) {
    const id = root.data.group;
    if (id === undefined) continue;
    buckets.set(id, [...(buckets.get(id) ?? []), root]);
  }
  if (buckets.size < 2) return null;
  const groups = [...buckets].sort(
    ([a, left], [b, right]) =>
      Number(a === "tests") - Number(b === "tests") ||
      right.length - left.length ||
      a.localeCompare(b),
  );
  const positions = new Map<string, Point>();
  const boxes = new Map<string, GroupBox>();
  const ordered = groups.map(([id, modules]) => {
    modules.sort(
      (a, b) =>
        (full.get(a.data.id)?.x ?? 0) - (full.get(b.data.id)?.x ?? 0) ||
        (full.get(a.data.id)?.y ?? 0) - (full.get(b.data.id)?.y ?? 0) ||
        a.data.id.localeCompare(b.data.id),
    );
    const local = new Map<string, Point>();
    let bottom = 0;
    for (let column = 0; column * 9 < modules.length; column += 1) {
      let y = 0;
      for (const module of modules.slice(column * 9, column * 9 + 9)) {
        const height = heights.get(module.data.id) ?? g.headerHeight;
        local.set(module.data.id, { x: (column + 0.5) * cellWidth, y: y + height / 2 });
        y += Math.max(cellHeight, height + 150);
      }
      bottom = Math.max(bottom, y);
    }
    return { id, local, width: Math.ceil(modules.length / 9) * cellWidth, height: bottom };
  });
  const band = Math.max(...ordered.map((group) => group.height));
  let x = 0,
    y = 0,
    columnWidth = 0;
  for (const group of ordered) {
    if (y && y + group.height > band) {
      x += columnWidth + 340;
      y = 0;
      columnWidth = 0;
    }
    boxes.set(group.id, { x, y, width: group.width, height: group.height });
    for (const [id, p] of group.local) positions.set(id, { x: x + p.x, y: y + p.y });
    columnWidth = Math.max(columnWidth, group.width);
    y += group.height + 460;
  }
  return { positions, boxes, cellWidth, cellHeight };
}

/** Translate a whole module together after the existing fold projection measured its height. */
export function applyGroupPack(
  roots: GraphElement[],
  children: ReadonlyMap<string, GraphElement[]>,
  full: ReadonlyMap<string, GroupBox>,
  heights: ReadonlyMap<string, number>,
  positions: Map<string, Point>,
  theme: GeometryTokens,
) {
  const packed = groupPack(roots, full, heights, theme);
  if (!packed) return;
  const translate = (id: string, dx: number, dy: number) => {
    const p = positions.get(id);
    if (p) positions.set(id, { x: p.x + dx, y: p.y + dy });
    for (const child of children.get(id) ?? []) translate(child.data.id, dx, dy);
  };
  for (const root of roots) {
    const p = positions.get(root.data.id),
      target = packed.positions.get(root.data.id);
    if (p && target) translate(root.data.id, target.x - p.x, target.y - p.y);
  }
}
