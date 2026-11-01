import type { Slot } from "@contracts";
import type { GraphElement } from "./elements";
import { foldPositions } from "./foldPositions";
import { nodeGeometry } from "./geometry";
import type { GeometryTokens } from "./geometry";

export interface MapModule {
  id: string;
  name: string;
  x: number;
  y: number;
  functions: number;
  agents: { slot: Slot; text: string }[];
}
export interface MapGroup {
  headerWidth?: number;
  id: string;
  label: string;
  panel: boolean;
  modules: MapModule[];
  x: number;
  y: number;
  width: number;
  height: number;
}
export interface MapModel {
  groups: MapGroup[];
  cellWidth: number;
  cellHeight: number;
}

/** Expanded horizontal bounds match the card frames, including nested class padding. */
function widestCard(elements: readonly GraphElement[], g: ReturnType<typeof nodeGeometry>) {
  const children = new Map<string, GraphElement[]>();
  for (const node of elements)
    if (node.group === "nodes" && node.position) {
      const parent = node.data.parent ?? "";
      children.set(parent, [...(children.get(parent) ?? []), node]);
    }
  const box = (node: GraphElement): { left: number; right: number } => {
    const kids = children.get(node.data.id) ?? [];
    if (!kids.length) {
      const width = node.data.w ?? g.rowWidth,
        x = node.position?.x ?? 0;
      return { left: x - width / 2, right: x + width / 2 };
    }
    const bounds = kids.map(box),
      pad = g.outer[node.data.kind === "class" ? "class" : "module"];
    return {
      left: Math.min(...bounds.map((b) => b.left)) - pad.left,
      right: Math.max(...bounds.map((b) => b.right)) + pad.right,
    };
  };
  return Math.max(
    g.rowWidth + g.outer.module.left + g.outer.module.right,
    ...(children.get("") ?? []).map((root) => {
      const b = box(root);
      return b.right - b.left;
    }),
  );
}

/** Read only original nodes: folded marks would count an agent's change more than once. */
export function mapModel(elements: readonly GraphElement[], theme: GeometryTokens): MapModel {
  const g = nodeGeometry(theme);
  const roots = elements.filter((e) => e.data.kind === "module");
  const folded = new Set(roots.map((e) => e.data.id));
  const { positions } = foldPositions(elements, new Set(), theme, folded);
  const byId = new Map(elements.filter((e) => e.group === "nodes").map((e) => [e.data.id, e]));
  const descendants = new Map<string, GraphElement[]>();
  for (const node of byId.values()) {
    let root = node;
    while (root.data.parent && byId.has(root.data.parent))
      root = byId.get(root.data.parent) ?? root;
    descendants.set(root.data.id, [...(descendants.get(root.data.id) ?? []), node]);
  }
  const cellWidth = widestCard(elements, g) + g.gutter;
  const cellHeight = g.headerHeight + 150;
  const groups = new Map<string, MapGroup>();
  for (const root of roots) {
    const p = positions.get(root.data.id);
    if (!p) continue;
    const id = root.data.group ?? root.data.id;
    const group = groups.get(id) ?? {
      id,
      label: id,
      panel: false,
      modules: [],
      x: Infinity,
      y: Infinity,
      width: 0,
      height: 0,
    };
    const nodes = descendants.get(root.data.id) ?? [];
    const agents = (["A", "B", "C", "D", "E", "F"] as const).flatMap((slot) => {
      const pending = nodes.filter((n) => n.classes.split(" ").includes(`pending-${slot}`));
      if (!pending.length) return [];
      const editing = pending.some((n) => n.classes.split(" ").includes(`focus-${slot}-edit`));
      return [
        {
          slot,
          text: `Agent ${slot} · ${editing ? "editing" : `${String(pending.length)} changed`}`,
        },
      ];
    });
    group.modules.push({
      id: root.data.id,
      name: root.data.title ?? root.data.id,
      x: p.x,
      y: p.y + (cellHeight - g.headerHeight) / 2,
      functions: nodes.filter((n) => n.data.kind === "function" || n.data.kind === "method").length,
      agents,
    });
    const x = p.x - cellWidth / 2,
      y = p.y - g.headerHeight / 2;
    const right = Math.max(group.x === Infinity ? 0 : group.x + group.width, x + cellWidth);
    const bottom = Math.max(group.y === Infinity ? 0 : group.y + group.height, y + cellHeight);
    group.x = Math.min(group.x, x);
    group.y = Math.min(group.y, y);
    group.width = right - group.x;
    group.height = bottom - group.y;
    groups.set(id, group);
  }
  for (const group of groups.values()) {
    group.panel =
      roots.some((r) => r.data.group === group.id) && group.id !== "" && group.modules.length > 1;
    if (!group.panel)
      for (const module of group.modules) {
        const root = byId.get(module.id);
        module.name =
          group.id && root?.data.group !== undefined ? `${group.id}/${module.name}` : module.name;
      }
  }
  return { groups: [...groups.values()], cellWidth, cellHeight };
}

/** Header type stays in screen pixels, including its wrapped second line at narrow zooms. */
export function panelBox(group: MapGroup, zoom: number, groups: readonly MapGroup[] = [group]) {
  const header = group.panel
    ? Math.max(
        ...groups
          .filter((g) => g.panel && g.y === group.y)
          .map((g) => (g.width * zoom + 12 < (g.headerWidth ?? 180) ? 60 : 37)),
      )
    : 0;
  return {
    id: group.id,
    x1: group.x - 6 / zoom,
    y1: group.y - header / zoom,
    x2: group.x + group.width + 6 / zoom,
    y2: group.y + group.height + 6 / zoom,
  };
}
