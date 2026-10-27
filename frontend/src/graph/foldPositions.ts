import type { Point } from "@contracts";

import type { GraphElement } from "./elements";
import { nodeGeometry } from "./geometry";
import type { GeometryTokens } from "./geometry";

interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Compact only a folded class's later siblings and the later modules in that preset column. */
export function foldPositions(
  elements: readonly GraphElement[],
  expanded: ReadonlySet<string>,
  theme: GeometryTokens,
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
  const visible = representatives(nodes, expanded);
  const place = (node: GraphElement, dy: number, hiddenBy?: string): number => {
    const box = full.get(node.data.id);
    if (!box) return 0;
    const folded = node.data.kind === "class" && !expanded.has(node.data.id);
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
  return { positions, visible };
}

function representatives(nodes: readonly GraphElement[], expanded: ReadonlySet<string>) {
  const visible = new Map<string, string>();
  const byId = new Map(nodes.map((node) => [node.data.id, node]));
  for (const node of nodes) {
    let ancestor = node;
    let id = node.data.id;
    while (ancestor.data.parent) {
      const parent = byId.get(ancestor.data.parent);
      if (!parent) break;
      if (parent.data.kind === "class" && !expanded.has(parent.data.id)) id = parent.data.id;
      ancestor = parent;
    }
    visible.set(node.data.id, id);
  }
  return visible;
}
