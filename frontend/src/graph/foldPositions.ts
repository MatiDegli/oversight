import type { Point } from "@contracts";

import type { GraphElement } from "./elements";
import { nodeGeometry } from "./geometry";
import type { GeometryTokens } from "./geometry";
import { packColumns } from "./packColumns";

interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Project from the expanded preset each time; unfolding never accumulates layout offsets. */
export function foldPositions(
  elements: readonly GraphElement[],
  expanded: ReadonlySet<string>,
  theme: GeometryTokens,
  foldedModules: ReadonlySet<string> = new Set(),
) {
  const g = nodeGeometry(theme);
  const nodes = elements.filter((e) => e.group === "nodes");
  const children = new Map<string, GraphElement[]>();
  for (const node of nodes) {
    if (!node.position) continue;
    const parent = node.data.parent ?? "";
    children.set(parent, [...(children.get(parent) ?? []), node]);
  }
  const full = new Map<string, Box>();
  const measure = (node: GraphElement): Box => {
    const kids = children.get(node.data.id) ?? [];
    const p = node.position ?? { x: 0, y: 0 };
    let box: Box;
    if (!kids.length) {
      const width = node.data.w ?? g.rowWidth;
      const height = node.data.h ?? g.rowHeight;
      box = { x: p.x - width / 2, y: p.y - height / 2, width, height };
    } else {
      const boxes = kids.map(measure);
      const pad = g.outer[node.data.kind === "class" ? "class" : "module"];
      const x = Math.min(...boxes.map((b) => b.x)) - pad.left;
      const y = Math.min(...boxes.map((b) => b.y)) - pad.top;
      box = {
        x,
        y,
        width: Math.max(...boxes.map((b) => b.x + b.width)) + pad.right - x,
        height: Math.max(...boxes.map((b) => b.y + b.height)) + pad.bottom - y,
      };
    }
    full.set(node.data.id, box);
    return box;
  };
  const roots = children.get("") ?? [];
  roots.forEach(measure);
  const positions = new Map<string, Point>();
  const heights = new Map<string, number>();
  const visible = representatives(nodes, expanded, foldedModules);
  const place = (node: GraphElement, dy: number, hiddenBy?: string): number => {
    const box = full.get(node.data.id);
    if (!box) return 0;
    const moduleFold = node.data.kind === "module" && foldedModules.has(node.data.id);
    const folded = moduleFold || (node.data.kind === "class" && !expanded.has(node.data.id));
    visible.set(node.data.id, hiddenBy ?? node.data.id);
    const kids = [...(children.get(node.data.id) ?? [])].sort(
      (a, b) =>
        (full.get(a.data.id)?.y ?? 0) - (full.get(b.data.id)?.y ?? 0) ||
        (a.data.id < b.data.id ? -1 : 1),
    );
    let removed = 0;
    for (const kid of kids)
      removed += place(kid, dy - removed, hiddenBy ?? (folded ? node.data.id : undefined));
    const height = folded ? g.headerHeight : box.height - removed;
    positions.set(node.data.id, { x: box.x + box.width / 2, y: box.y + dy + height / 2 });
    heights.set(node.data.id, height);
    return box.height - height;
  };
  const columns = new Map<number, GraphElement[]>();
  for (const root of roots) {
    const x = full.get(root.data.id)?.x ?? 0;
    columns.set(x, [...(columns.get(x) ?? []), root]);
  }
  for (const column of columns.values()) {
    column.sort((a, b) => (full.get(a.data.id)?.y ?? 0) - (full.get(b.data.id)?.y ?? 0));
    let removed = 0;
    for (const root of column) removed += place(root, -removed);
  }
  if (foldedModules.size) boundColumns(roots, children, full, heights, positions, g);
  return { positions, visible };
}

function representatives(
  nodes: readonly GraphElement[],
  expanded: ReadonlySet<string>,
  foldedModules: ReadonlySet<string>,
) {
  const visible = new Map<string, string>();
  const byId = new Map(nodes.map((node) => [node.data.id, node]));
  for (const node of nodes) {
    let ancestor = node;
    let id = node.data.id;
    while (ancestor.data.parent) {
      const parent = byId.get(ancestor.data.parent);
      if (!parent) break;
      if (
        (parent.data.kind === "class" && !expanded.has(parent.data.id)) ||
        foldedModules.has(parent.data.id)
      )
        id = parent.data.id;
      ancestor = parent;
    }
    visible.set(node.data.id, id);
  }
  return visible;
}

/** Reflow only when a projected column exceeds nine header slots, keeping small presets intact. */
function boundColumns(
  roots: GraphElement[],
  children: ReadonlyMap<string, GraphElement[]>,
  full: ReadonlyMap<string, Box>,
  heights: ReadonlyMap<string, number>,
  positions: Map<string, Point>,
  g: ReturnType<typeof nodeGeometry>,
) {
  const maxHeight = 9 * (g.headerHeight + g.rowGap);
  const cards = roots.map((root) => ({
    id: root.data.id,
    width: full.get(root.data.id)?.width ?? g.rowWidth,
    height: heights.get(root.data.id) ?? g.headerHeight,
  }));
  const columns = new Map<number, { top: number; bottom: number }>();
  for (const card of cards) {
    const p = positions.get(card.id);
    if (!p) continue;
    const x = full.get(card.id)?.x ?? 0;
    const old = columns.get(x);
    columns.set(x, {
      top: Math.min(old?.top ?? Infinity, p.y - card.height / 2),
      bottom: Math.max(old?.bottom ?? -Infinity, p.y + card.height / 2),
    });
  }
  if ([...columns.values()].every((c) => c.bottom - c.top <= maxHeight)) return;
  cards.sort((a, b) => {
    const left = full.get(a.id),
      right = full.get(b.id);
    return (
      (left?.x ?? 0) - (right?.x ?? 0) ||
      (left?.y ?? 0) - (right?.y ?? 0) ||
      a.id.localeCompare(b.id)
    );
  });
  const packed = packColumns(cards, maxHeight, g.gutter, g.rowGap);
  const translate = (id: string, dx: number, dy: number) => {
    const point = positions.get(id);
    if (point) positions.set(id, { x: point.x + dx, y: point.y + dy });
    for (const child of children.get(id) ?? []) translate(child.data.id, dx, dy);
  };
  for (const card of cards) {
    const before = positions.get(card.id),
      after = packed.get(card.id);
    if (before && after) translate(card.id, after.x - before.x, after.y - before.y);
  }
}
