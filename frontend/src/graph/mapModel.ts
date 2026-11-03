import type { AgentMark } from "./agentMarks";
import { combineMarks } from "./agentMarks";
import type { AgentPresentation } from "../teams/presentation";
import { displayName } from "./mapEvidence";
import type { GraphElement } from "./elements";
import { foldPositions } from "./foldPositions";
import { nodeGeometry } from "./geometry";
import type { GeometryTokens } from "./geometry";

export interface MapModule {
  id: string;
  name: string;
  fullName?: string;
  nameWidth?: number;
  inCone?: boolean;
  distance?: number;
  x: number;
  y: number;
  functions: number;
  agents: (AgentMark & { text: string })[];
}
export interface MapGroup {
  headerWidth?: number;
  headerItemWidths?: number[];
  headerLines?: number;
  rings?: AgentPresentation[];
  decisions?: number;
  tested?: number;
  coneCount?: number;
  coneTotal?: number;
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
  const cellHeight = Math.max(g.headerHeight + 150, 350);
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
    const agents = (
      combineMarks(nodes.flatMap((node) => node.data.marks?.badges ?? []))?.badges ?? []
    ).map((mark) => ({
      ...mark,
      text: `${mark.planContext?.shortLabel ?? mark.label} ${mark.work === "edit" ? "editing" : mark.ready ? "ready" : mark.work === "read" ? "reading" : mark.word}`,
    }));
    group.modules.push({
      id: root.data.id,
      ...displayName(root.data.title ?? root.data.id, id),
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
          .map((g) => 37 + 23 * (headerLines(g, zoom) - 1)),
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

/** Flex items retain their measured widths while the screen-sized strip wraps. */
export function headerLines(group: MapGroup, zoom: number) {
  if (!group.headerItemWidths)
    return group.headerLines ?? (group.width * zoom + 12 < (group.headerWidth ?? 180) ? 2 : 1);
  let lines = 1,
    used = 0;
  // The panel adds 12px outside the ground, then loses 24px padding and 2px border.
  const width = group.width * zoom - 14;
  for (const item of group.headerItemWidths) {
    if (used && used + 10 + item > width) {
      lines++;
      used = 0;
    }
    used += (used ? 10 : 0) + item;
  }
  return lines;
}

/** Leave two pixels between the outer ownership ring and the module's name. */
export function ringClearance(module: MapModule) {
  const rings = module.agents.reduce(
    (count, agent) => count + Number(!!agent.work) + Number(agent.pending),
    0,
  );
  return rings ? 4 * rings : 0;
}

/** The block's retained text boxes keep its circle stationary when names are hidden. */
export function moduleLayout(
  module: MapModule,
  model: MapModel,
  zoom: number,
  pan: { x: number; y: number },
) {
  const radius = Math.max(5, Math.min(9, Math.round(model.cellHeight * zoom * 0.1)));
  const agentLines = module.agents.reduce((count, agent) => count + (agent.planContext ? 2 : 1), 0);
  const height = 2 * radius + 3 + 15.6 + 14.4 * (1 + agentLines) + ringClearance(module);
  const x = module.x * zoom + pan.x,
    top = module.y * zoom + pan.y - height / 2;
  const width = model.cellWidth * zoom - 10;
  return {
    x,
    y: top + radius,
    radius,
    outerRadius: radius + (module.agents.length ? 5 : 0),
    x1: x - width / 2,
    y1: top,
    x2: x + width / 2,
    y2: top + height,
    name: {
      x1: x - Math.min(width, module.nameWidth ?? width) / 2,
      y1: top + 2 * radius + 3,
      x2: x + Math.min(width, module.nameWidth ?? width) / 2,
      y2: top + height,
    },
  };
}
