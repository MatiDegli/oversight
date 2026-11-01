import type { GraphElement } from "./elements";
import { moduleOwners } from "./mapEvidence";
import type { Box } from "./camera";
import type { MapModel } from "./mapModel";
import { moduleLayout, panelBox } from "./mapModel";

export type MapHover = { kind: "module" | "group"; id: string };
export interface MapViewport {
  model: MapModel;
  zoom: number;
  pan: { x: number; y: number };
}
const contains = (box: Box, p: { x: number; y: number }) =>
  p.x >= box.x1 && p.x <= box.x2 && p.y >= box.y1 && p.y <= box.y2;

/** Input stays on the graph stage; this mirrors the pointer-free layer's screen boxes. */
export function mapHit(point: { x: number; y: number }, frame: MapViewport): MapHover | undefined {
  const { model, zoom, pan } = frame;
  for (const group of model.groups)
    for (const module of group.modules)
      if (contains(moduleLayout(module, model, zoom, pan), point))
        return { kind: "module", id: module.id };
  const world = { x: (point.x - pan.x) / zoom, y: (point.y - pan.y) / zoom };
  const group = model.groups.find(
    (g) => g.panel && contains(panelBox(g, zoom, model.groups), world),
  );
  return group ? { kind: "group", id: group.id } : undefined;
}

export interface MapCall {
  source: string;
  target: string;
  count: number;
  possible: boolean;
}

/** Same directional exact totals and unordered possible suppression as folded cards. */
export function mapCalls(elements: readonly GraphElement[]): MapCall[] {
  const owners = moduleOwners(elements),
    pairs = new Map<string, MapCall & { rest: boolean }>();
  for (const edge of elements) {
    if (edge.group !== "edges" || edge.data.kind !== "calls") continue;
    const source = owners.get(edge.data.source ?? ""),
      target = owners.get(edge.data.target ?? "");
    if (!source || !target || source === target) continue;
    const key = `${source}>${target}`,
      classes = edge.classes.split(" ");
    const call = pairs.get(key) ?? { source, target, count: 0, possible: true, rest: false };
    if (classes.includes("exact")) {
      call.count++;
      call.possible = false;
    }
    if (classes.includes("possible") && edge.data.restPossible) call.rest = true;
    pairs.set(key, call);
  }
  const exact = new Set(
    [...pairs.values()]
      .filter((c) => !c.possible)
      .map((c) => [c.source, c.target].sort().join(">")),
  );
  const faint = new Set<string>();
  return [...pairs.values()].filter((c) => {
    if (!c.possible) return true;
    const key = [c.source, c.target].sort().join(">");
    if (!c.rest || exact.has(key) || faint.has(key)) return false;
    faint.add(key);
    return true;
  });
}

export const chipFractions = [0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8, 0.12, 0.88];
const overlaps = (a: Box, b: Box) => a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1;
type Circle = { x: number; y: number; radius: number };
export function placeChip(
  start: { x: number; y: number },
  end: { x: number; y: number },
  width: number,
  names: Box[],
  circles: Circle[],
  placed: Box[],
) {
  for (const fraction of chipFractions) {
    const x = start.x + (end.x - start.x) * fraction,
      y = start.y + (end.y - start.y) * fraction;
    const box = { x1: x - width / 2 - 2, y1: y - 10, x2: x + width / 2 + 2, y2: y + 10 };
    if ([...names, ...placed].some((b) => overlaps(box, b))) continue;
    if (
      circles.some(
        (c) =>
          Math.hypot(
            c.x - Math.max(box.x1, Math.min(c.x, box.x2)),
            c.y - Math.max(box.y1, Math.min(c.y, box.y2)),
          ) <
          c.radius + 3,
      )
    )
      continue;
    return { x, y, box, fraction };
  }
  return undefined;
}

/** Insets are screen pixels measured from the outside of the pending ring, when present. */
export function endPoints(a: Circle, b: Circle, possible = false) {
  const length = Math.hypot(b.x - a.x, b.y - a.y),
    ux = (b.x - a.x) / length,
    uy = (b.y - a.y) / length;
  return {
    start: {
      x: a.x + ux * (a.radius + (possible ? 2 : 3)),
      y: a.y + uy * (a.radius + (possible ? 2 : 3)),
    },
    end: {
      x: b.x - ux * (b.radius + (possible ? 2 : 4)),
      y: b.y - uy * (b.radius + (possible ? 2 : 4)),
    },
  };
}

export function pointed(moduleId: string, groupId: string, hover?: MapHover) {
  return hover?.kind === "module"
    ? hover.id === moduleId
    : hover?.kind === "group" && hover.id === groupId;
}

/** Keep directions explicit: exact hover arrows terminate at the callee. */
export function mapSegments(frame: MapViewport & { calls?: MapCall[]; hover?: MapHover }) {
  const { model, zoom, pan, hover } = frame;
  const modules = model.groups.flatMap((g) =>
    g.modules.map((m) => ({ ...m, group: g.id, layout: moduleLayout(m, model, zoom, pan) })),
  );
  const byId = new Map(modules.map((m) => [m.id, m]));
  const names = modules
    .filter((m) => m.inCone !== false || m.agents.length || pointed(m.id, m.group, hover))
    .map((m) => {
      const full = pointed(m.id, m.group, hover);
      return {
        ...m.layout.name,
        x1: full ? m.layout.x - (m.nameWidth ?? 0) / 2 : m.layout.name.x1,
        x2: full ? m.layout.x + (m.nameWidth ?? 0) / 2 : m.layout.name.x2,
      };
    });
  const circles = modules.map((m) => ({
      x: m.layout.x,
      y: m.layout.y,
      radius: m.layout.outerRadius,
    })),
    placed: Box[] = [];
  return (frame.calls ?? []).flatMap((call) => {
    const source = byId.get(call.source),
      target = byId.get(call.target);
    if (!source || !target) return [];
    const shown =
      hover?.kind === "module"
        ? hover.id === source.id || hover.id === target.id
        : hover?.kind === "group" && (source.group === hover.id) !== (target.group === hover.id);
    const cone = !call.possible && source.inCone === true && target.inCone === true;
    if (!shown && !cone) return [];
    const a = (cone ? target : source).layout,
      b = (cone ? source : target).layout;
    const { start, end } = endPoints(
      { ...a, radius: a.outerRadius },
      { ...b, radius: b.outerRadius },
      call.possible,
    );
    const chip =
      !call.possible && call.count > 1
        ? placeChip(start, end, 12 + String(call.count).length * 7, names, circles, placed)
        : undefined;
    if (chip) placed.push(chip.box);
    return [{ ...call, cone, start, end, chip }];
  });
}

/** Seeds and agent marks keep their own signal, without a distance badge. */
export function distanceBadge(module: import("./mapModel").MapModule) {
  return module.agents.length ? undefined : module.distance;
}
