import type { Core, Position } from "cytoscape";

import type { GraphElement } from "./elements";

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
const boxAt = ({ x, y }: Position, padding = 0): Box => ({
  x1: x - 75 - padding,
  x2: x + 75 + padding,
  y1: y - 30 - padding,
  y2: y + 30 + padding,
});

/** Measure before batching: compound bounds are stale while Cytoscape batches writes. */
function placeAdditions(cy: Core, elements: GraphElement[]): GraphElement[] {
  const nodes = new Map(elements.filter((e) => e.group === "nodes").map((e) => [e.data.id, e]));
  const boxes = new Map<string, Box>();
  const occupied = new Map<string, Box>();
  for (const node of cy.nodes()) {
    if (!nodes.has(node.id())) continue;
    const box = node.boundingBox({ includeLabels: true, includeOverlays: false });
    boxes.set(node.id(), box);
    occupied.set(node.id(), box);
  }
  const ancestors = (element: GraphElement): string[] => {
    const ids: string[] = [];
    let parent = element.data.parent;
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
    const parents = ancestors(element);
    const parent = nodes.get(parents[0] ?? "");
    if (parent) place(parent);
    const rootId = parents.at(-1) ?? id;
    const rootBox = boxes.get(rootId);
    const foreign = [...boxes]
      .filter(
        ([otherId, box]) =>
          !nodes.get(otherId)?.data.parent &&
          otherId !== rootId &&
          // Existing overlaps cannot be repaired without moving the preset.
          (!rootBox || !intersects(rootBox, box)),
      )
      .map(([, box]) => box);
    const anchor = boxes.get(parent?.data.id ?? "") ?? boxAt({ x: 0, y: 0 });
    const center = { x: (anchor.x1 + anchor.x2) / 2, y: (anchor.y1 + anchor.y2) / 2 };
    const fits = (point: Position): boolean => {
      const bounds = boxAt(point, parents.length * 31);
      const grown = rootBox ? union(rootBox, bounds) : bounds;
      return (
        !foreign.some((box) => intersects(grown, box)) &&
        ![...occupied].some(
          ([otherId, box]) => !parents.includes(otherId) && intersects(boxAt(point, 10), box),
        )
      );
    };
    let position = element.position;
    if (!position) {
      // Prefer free space inside the parent's box, then grow around its perimeter.
      const inside: Position[] = [];
      for (let x = anchor.x1 + 106; x <= anchor.x2 - 106; x += 170)
        for (let y = anchor.y1 + 61; y <= anchor.y2 - 61; y += 80) inside.push({ x, y });
      position = inside.find(fits);
      for (let ring = 0; !position && ring < 100; ring++) {
        const candidates: Position[] = [];
        for (let offset = -ring; offset <= ring; offset++) {
          candidates.push(
            { x: anchor.x2 + 106 + ring * 170, y: center.y + offset * 80 },
            { x: anchor.x1 - 106 - ring * 170, y: center.y + offset * 80 },
            { x: center.x + offset * 170, y: anchor.y2 + 61 + ring * 80 },
            { x: center.x + offset * 170, y: anchor.y1 - 61 - ring * 80 },
          );
        }
        position = candidates.find(fits);
      }
      // If surrounding modules leave no growth corridor, keep the parent's box intact.
      position ??= center;
    }
    const result = { ...element, position };
    placed.set(id, result);
    const bounds = boxAt(position);
    boxes.set(id, bounds);
    occupied.set(id, bounds);
    for (const [depth, parentId] of parents.entries()) {
      const previous = boxes.get(parentId);
      const expanded = boxAt(position, (depth + 1) * 31);
      boxes.set(parentId, previous ? union(previous, expanded) : expanded);
    }
    return result;
  };
  return elements.filter((e) => cy.getElementById(e.data.id).empty()).map(place);
}

/** Update by id, retaining dragged positions and the viewport after the first layout. */
export function updateGraph(cy: Core, elements: GraphElement[]): void {
  const initial = cy.nodes().empty();
  const byId = new Map(elements.map((element) => [element.data.id, element]));
  const added = placeAdditions(cy, elements);
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
      padding: 40,
    };
    cy.layout(preset ? { name: "preset", fit: true, padding: 40 } : fallback).run();
  }
}
