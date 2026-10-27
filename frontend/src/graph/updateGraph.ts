import type { Core, NodeCollection, Position } from "cytoscape";

import { getActiveTheme } from "../theme";

import type { GraphElement } from "./elements";
import { FIT_PADDING, nodeGeometry } from "./geometry";

type GeometryTheme = Parameters<typeof nodeGeometry>[0];

interface Box {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

const intersects = (a: Box, b: Box) => a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1;
const union = (a: Box, b: Box): Box => ({
  x1: Math.min(a.x1, b.x1),
  y1: Math.min(a.y1, b.y1),
  x2: Math.max(a.x2, b.x2),
  y2: Math.max(a.y2, b.y2),
});

// A compound's centre follows its children. Keep the last position it had as a leaf separately,
// including human drags, so removing its last child does not leave it at the frame's centre.
const leafPositions = new WeakMap<Core, Map<string, Position>>();
/**
 * Measure before batching: compound bounds are stale while Cytoscape batches writes.
 *
 * A new node goes where its own box, and the box of every ancestor grown to hold it, clear the
 * nodes that ancestor does not contain. A class with no children is drawn at leaf size among
 * tightly packed siblings; with its first child it becomes a frame around that child, with
 * padding and a label, which no longer fits its old slot. The child therefore goes to the nearest
 * free space and the frame moves with it. Existing leaves never move.
 */
function placeAdditions(cy: Core, elements: GraphElement[], theme: GeometryTheme): GraphElement[] {
  const g = nodeGeometry(theme);
  // Cytoscape's bounding boxes are a little larger than the drawn shapes (strokes, rounding), so
  // the sizes come from a measured leaf and parent when the graph has them, and from the tokens
  // (the drawn size plus its border) when it does not.
  const bounds = (collection: Pick<NodeCollection, "boundingBox">) =>
    collection.boundingBox({ includeLabels: false, includeOverlays: false });
  const leaf = cy.nodes(":childless").first();
  const frame = cy.nodes(":parent").first();
  const halfWidth = leaf.nonempty() ? bounds(leaf).w / 2 : (g.width + g.border) / 2;
  const halfHeight = leaf.nonempty() ? bounds(leaf).h / 2 : (g.height + g.border) / 2;
  const inset = frame.nonempty() ? bounds(frame.children()).x1 - bounds(frame).x1 : g.inset;
  const boxAt = ({ x, y }: Position, padding = 0): Box => ({
    x1: x - halfWidth - padding,
    x2: x + halfWidth + padding,
    y1: y - halfHeight - padding,
    y2: y + halfHeight + padding,
  });
  /** The box an ancestor `depth` levels up needs to hold a leaf at `point`, with its label. */
  const reach = (point: Position, depth: number): Box => {
    const box = boxAt(point, (depth + 1) * inset);
    return { ...box, y1: box.y1 - g.label };
  };
  const nodes = new Map(elements.filter((e) => e.group === "nodes").map((e) => [e.data.id, e]));
  const boxes = new Map<string, Box>();
  // Parents drawn at leaf size now: their first child replaces that box instead of extending it.
  const childless = new Set<string>();
  for (const node of cy.nodes()) {
    if (!nodes.has(node.id())) continue;
    boxes.set(node.id(), node.boundingBox({ includeLabels: true, includeOverlays: false }));
    if (node.isChildless()) childless.add(node.id());
  }
  const ancestorsOf = (id: string): string[] => {
    const ids: string[] = [];
    let parent = nodes.get(id)?.data.parent;
    while (parent && !ids.includes(parent)) {
      ids.push(parent);
      parent = nodes.get(parent)?.data.parent;
    }
    return ids;
  };
  const placed = new Map<string, GraphElement>();
  const place = (element: GraphElement): GraphElement => {
    const id = element.data.id;
    const cached = placed.get(id);
    if (cached) return cached;
    if (element.group === "edges" || cy.getElementById(id).nonempty()) return element;
    const parents = ancestorsOf(id);
    const parent = nodes.get(parents[0] ?? "");
    if (parent) place(parent);
    /** An ancestor's box once it holds a leaf at `point`. */
    const grownAt = (parentId: string, depth: number, point: Position): Box => {
      const current = boxes.get(parentId);
      const reached = reach(point, depth);
      return current && !childless.has(parentId) ? union(current, reached) : reached;
    };
    // What each ancestor, once grown, must keep clear of: every node it neither contains nor sits
    // inside. Existing overlaps cannot be repaired without moving the preset, so a node that
    // already overlaps the ancestor's frame is left out.
    const avoid = parents.map((parentId) => {
      const current = boxes.get(parentId);
      const family = new Set([parentId, ...ancestorsOf(parentId)]);
      return [...boxes]
        .filter(
          ([otherId, box]) =>
            !family.has(otherId) &&
            !ancestorsOf(otherId).includes(parentId) &&
            (!current || childless.has(parentId) || !intersects(current, box)),
        )
        .map(([, box]) => box);
    });
    const anchor = boxes.get(parent?.data.id ?? "") ?? boxAt({ x: 0, y: 0 });
    const center = { x: (anchor.x1 + anchor.x2) / 2, y: (anchor.y1 + anchor.y2) / 2 };
    const fits = (point: Position): boolean => {
      const own = boxAt(point, g.margin);
      for (const [otherId, box] of boxes)
        if (!parents.includes(otherId) && intersects(own, box)) return false;
      return parents.every(
        (parentId, depth) =>
          !avoid[depth]?.some((box) => intersects(grownAt(parentId, depth, point), box)),
      );
    };
    const nearest = (points: Position[]) =>
      points
        .map((point) => ({ point, distance: Math.hypot(point.x - center.x, point.y - center.y) }))
        .sort((a, b) => a.distance - b.distance)
        .map(({ point }) => point);
    let position = element.position;
    if (!position) {
      // Prefer free space inside the parent's box, then grow around its perimeter, ring by ring.
      const edgeX = halfWidth + inset;
      const edgeY = halfHeight + inset;
      const inside: Position[] = [];
      for (let x = anchor.x1 + edgeX; x <= anchor.x2 - edgeX; x += g.gap)
        for (let y = anchor.y1 + edgeY; y <= anchor.y2 - edgeY; y += g.gap) inside.push({ x, y });
      position = nearest(inside).find(fits);
      // Search below first, then beside the module, and above last to spare its heading.
      const module = parents.find((id) => nodes.get(id)?.data.kind === "module");
      const boundary = boxes.get(module ?? "") ?? anchor;
      const direction = (point: Position) => {
        const box = boxAt(point);
        return box.y1 > boundary.y2 ? 0 : box.x2 < boundary.x1 || box.x1 > boundary.x2 ? 1 : 2;
      };
      for (let preferred = 0; !position && preferred < 3; preferred++) {
        for (let ring = 0; !position && ring < 100; ring++) {
          const far = ring * g.gap;
          const [x1, x2] = [anchor.x1 - edgeX - far, anchor.x2 + edgeX + far];
          const [y1, y2] = [anchor.y1 - edgeY - far, anchor.y2 + edgeY + far];
          const candidates: Position[] = [];
          for (let x = x1; x <= x2; x += g.gap) candidates.push({ x, y: y1 }, { x, y: y2 });
          for (let y = y1 + g.gap; y < y2; y += g.gap) candidates.push({ x: x1, y }, { x: x2, y });
          position = nearest(candidates.filter((point) => direction(point) === preferred)).find(
            fits,
          );
        }
      }
      // Beyond the bounded search, put the new leaf and its frames below every occupied box.
      // Unlike the parent's centre, this spot cannot overlap the surrounding nodes.
      if (!position) {
        const bottom = Math.max(0, ...[...boxes.values()].map((box) => box.y2));
        position = {
          x: center.x,
          y: bottom + halfHeight + (parents.length + 1) * inset + g.label + g.margin,
        };
      }
    }
    const result = { ...element, position };
    placed.set(id, result);
    boxes.set(id, boxAt(position));
    for (const [depth, parentId] of parents.entries()) {
      boxes.set(parentId, grownAt(parentId, depth, position));
      childless.delete(parentId);
    }
    return result;
  };
  return elements.filter((e) => cy.getElementById(e.data.id).empty()).map(place);
}

/** Update by id, retaining dragged positions and the viewport after the first layout. */
export function updateGraph(
  cy: Core,
  elements: GraphElement[],
  theme: GeometryTheme = getActiveTheme(),
): void {
  const initial = cy.nodes().empty();
  const byId = new Map(elements.map((element) => [element.data.id, element]));
  const remembered = leafPositions.get(cy) ?? new Map<string, Position>();
  leafPositions.set(cy, remembered);
  for (const id of remembered.keys()) if (!byId.has(id)) remembered.delete(id);
  for (const node of cy.nodes(":childless"))
    if (byId.has(node.id())) remembered.set(node.id(), { ...node.position() });
  const parents = new Set(elements.map((element) => element.data.parent));
  const restore = cy
    .nodes(":parent")
    .filter((node) => byId.has(node.id()) && !parents.has(node.id()) && remembered.has(node.id()));
  const added = placeAdditions(cy, elements, theme);
  cy.batch(() => {
    // Detach survivors before removing a parent (which would remove its children too).
    for (const node of cy.nodes()) {
      const definition = byId.get(node.id());
      if (definition && node.parent().first().id() !== definition.data.parent) {
        node.move({ parent: null });
      }
    }
    cy.elements()
      .filter((element) => !byId.has(element.id()))
      .remove();
    for (const node of restore) {
      const position = remembered.get(node.id());
      if (position) node.position(position);
    }
    cy.add(added);
    for (const definition of elements) {
      const element = cy.getElementById(definition.data.id);
      if (definition.group === "nodes") {
        if (element.parent().first().id() !== definition.data.parent) {
          element.move({ parent: definition.data.parent ?? null });
        }
      } else if (
        element.source().id() !== definition.data.source ||
        element.target().id() !== definition.data.target
      ) {
        element.move({ source: definition.data.source, target: definition.data.target });
      }
      element.data(definition.data);
      element.classes(definition.classes);
    }
  });
  if (initial && cy.nodes().nonempty()) {
    const preset = elements.some((element) => element.position !== undefined);
    const fallback = {
      name: "fcose",
      animate: false,
      fit: true,
      padding: FIT_PADDING,
    };
    cy.layout(preset ? { name: "preset", fit: true, padding: FIT_PADDING } : fallback).run();
  }
}
