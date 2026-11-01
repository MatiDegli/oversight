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
