import type { Core, NodeCollection, Position } from "cytoscape";

import type { GraphElement } from "./elements";
import { nodeGeometry } from "./geometry";

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

// Keep the person's layout until all additions that needed a fallback go away.
interface GrowthLayout {
  positions: Map<string, Position>;
  additions: Set<string>;
}
export const growthPositions = new WeakMap<Core, Map<string, GrowthLayout>>();
/**
 * Measure before batching: compound bounds are stale while Cytoscape batches writes.
 *
 * A new node goes where its own box, and the box of every ancestor grown to hold it, clear the
 * nodes that ancestor does not contain. A class with no children is drawn at leaf size among
 * tightly packed siblings; with its first child it becomes a frame around that child, with
 * padding and a label, which no longer fits its old slot. The child therefore goes to the nearest
 * free space and the frame moves with it. Leaves stay put whenever their root can grow; an
 * impossible fit relocates only that root, preserving the relative layout of all its leaves.
 */
export function placeAdditions(
  cy: Core,
  elements: GraphElement[],
  theme: Parameters<typeof nodeGeometry>[0],
) {
  const relocated = new Map<string, Position>();
  const g = nodeGeometry(theme);
  // Cytoscape's bounding boxes are a little larger than the drawn shapes (strokes, rounding), so
  // the sizes come from a measured leaf and parent when the graph has them, and from the tokens
  // (the drawn size plus its border) when it does not.
  const bounds = (collection: Pick<NodeCollection, "boundingBox">) =>
    collection.boundingBox({ includeLabels: false, includeOverlays: false });
  // A row is a function or method; frames with nothing in them are boxes of their own size.
  const leaf = cy.nodes(".function, .method").first();
  const measured = {
    width: leaf.nonempty() ? bounds(leaf).w / 2 : g.rowWidth / 2,
    height: leaf.nonempty() ? bounds(leaf).h / 2 : g.rowHeight / 2,
  };
  /** Half the box a node takes: its own size when elements.ts gave it one, a row's otherwise. */
  const halfOf = (element: GraphElement) => {
    const { w, h } = element.data;
    return {
      width: w === undefined ? measured.width : w / 2 + g.border,
      height: h === undefined ? measured.height : h / 2 + g.border,
    };
  };
  type Half = ReturnType<typeof halfOf>;
  const boxAt = ({ x, y }: Position, half: Half, padding = 0): Box => ({
    x1: x - half.width - padding,
    x2: x + half.width + padding,
    y1: y - half.height - padding,
    y2: y + half.height + padding,
  });
  /** What a frame of this kind holds around its children: a module's, or a class's. */
  const padOf = (kind: string | undefined) => (kind === "class" ? g.outer.class : g.outer.module);
  /**
   * The padding the ancestors up to `depth` levels up add around a row, side by side: the first
   * ancestor's, then the next one's, and so on. Each frame's drawn border adds its width to every
   * side, which Cytoscape's bounding box counts and the padding tokens do not.
   */
  const around = (kinds: readonly (string | undefined)[], depth: number) => {
    const sum = { top: 0, left: 0, right: 0, bottom: 0 };
    for (const kind of kinds.slice(0, depth + 1)) {
      const pad = padOf(kind);
      sum.top += pad.top + g.border;
      sum.left += pad.left + g.border;
      sum.right += pad.right + g.border;
      sum.bottom += pad.bottom + g.border;
    }
    return sum;
  };
  /** The box an ancestor `depth` levels up needs to hold a row at `point`, with its header band. */
  const reach = (
    point: Position,
    half: Half,
    kinds: readonly (string | undefined)[],
    depth: number,
  ): Box => {
    const pad = around(kinds, depth);
    const box = boxAt(point, half);
    return {
      x1: box.x1 - pad.left,
      x2: box.x2 + pad.right,
      y1: box.y1 - pad.top,
      y2: box.y2 + pad.bottom,
    };
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
    const kinds = parents.map((parentId) => nodes.get(parentId)?.data.kind);
    const parent = nodes.get(parents[0] ?? "");
    if (parent) place(parent);
    const half = halfOf(element);
    /** An ancestor's box once it holds a leaf at `point`. */
    const grownAt = (parentId: string, depth: number, point: Position): Box => {
      const current = boxes.get(parentId);
      const reached = reach(point, half, kinds, depth);
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
    const anchor = boxes.get(parent?.data.id ?? "") ?? boxAt({ x: 0, y: 0 }, half);
    const center = { x: (anchor.x1 + anchor.x2) / 2, y: (anchor.y1 + anchor.y2) / 2 };
    const fits = (point: Position): boolean => {
      const own = boxAt(point, half, g.margin);
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
      const pad = around(kinds, 0);
      const inside: Position[] = [];
      for (
        let x = anchor.x1 + half.width + pad.left;
        x <= anchor.x2 - half.width - pad.right;
        x += g.gap
      )
        for (
          let y = anchor.y1 + half.height + pad.top;
          y <= anchor.y2 - half.height - pad.bottom;
          y += g.gap
        )
          inside.push({ x, y });
      position = nearest(inside).find(fits);
      // Search below first, then beside the module, and above last to spare its heading.
      const module = parents.find((id) => nodes.get(id)?.data.kind === "module");
      const boundary = boxes.get(module ?? "") ?? anchor;
      const direction = (point: Position) => {
        const box = boxAt(point, half);
        return box.y1 > boundary.y2 ? 0 : box.x2 < boundary.x1 || box.x1 > boundary.x2 ? 1 : 2;
      };
      // A grid can miss a narrow opening. Clearance boundaries give a finite exact search,
      // run within each direction so an available side still wins over space above the heading.
      let exact: Position[] | undefined;
      const exactCandidates = () => {
        const xs = new Set([center.x]);
        const ys = new Set([center.y]);
        const boundaries = (box: Box, left: number, right: number, top: number, bottom: number) => {
          xs.add(box.x1 - left);
          xs.add(box.x2 + right);
          ys.add(box.y1 - top);
          ys.add(box.y2 + bottom);
        };
        for (const box of boxes.values())
          boundaries(
            box,
            half.width + g.margin,
            half.width + g.margin,
            half.height + g.margin,
            half.height + g.margin,
          );
        for (const [depth, obstacles] of avoid.entries()) {
          const pad = around(kinds, depth);
          for (const box of obstacles)
            boundaries(
              box,
              half.width + pad.right,
              half.width + pad.left,
              half.height + pad.bottom,
              half.height + pad.top,
            );
        }
        return [...xs].flatMap((x) => [...ys].map((y) => ({ x, y })));
      };
      for (let preferred = 0; !position && preferred < 3; preferred++) {
        for (let ring = 0; !position && ring < 100; ring++) {
          const far = ring * g.gap;
          const [x1, x2] = [
            anchor.x1 - half.width - pad.left - far,
            anchor.x2 + half.width + pad.right + far,
          ];
          const [y1, y2] = [
            anchor.y1 - half.height - pad.top - far,
            anchor.y2 + half.height + pad.bottom + far,
          ];
          const candidates: Position[] = [];
          for (let x = x1; x <= x2; x += g.gap) candidates.push({ x, y: y1 }, { x, y: y2 });
          for (let y = y1 + g.gap; y < y2; y += g.gap) candidates.push({ x: x1, y }, { x: x2, y });
          position = nearest(candidates.filter((point) => direction(point) === preferred)).find(
            fits,
          );
        }
        if (!position) {
          exact ??= exactCandidates();
          position = nearest(exact.filter((point) => direction(point) === preferred)).find(fits);
        }
      }

      if (!position) {
        // Sometimes an anchored frame cannot grow at all (e.g. a new nested class is wider than
        // the corridor between its neighbours). Move only that root's subtree to clear space,
        // retaining all relative positions; placing just the leaf below would stretch its frame
        // across the neighbours. Other roots and their leaves keep their exact positions.
        const root = parents.at(-1);
        const bottom = Math.max(0, ...[...boxes.values()].map((box) => box.y2));
        position = {
          x: center.x,
          y:
            (boxes.get(root ?? "")?.y2 ?? bottom) +
            half.height +
            around(kinds, parents.length - 1).bottom +
            around(kinds, parents.length - 1).top +
            g.margin,
        };
        if (root) {
          const history = growthPositions.get(cy) ?? new Map<string, GrowthLayout>();
          growthPositions.set(cy, history);
          const saved = history.get(root) ?? {
            positions: new Map<string, Position>(),
            additions: new Set<string>(),
          };
          history.set(root, saved);
          for (const candidate of nodes.keys())
            if (cy.getElementById(candidate).empty() && ancestorsOf(candidate).includes(root))
              saved.additions.add(candidate);
          const grown = grownAt(root, parents.length - 1, position);
          const dy = bottom + g.margin - grown.y1;
          const family = [...boxes.keys()].filter(
            (other) => other === root || ancestorsOf(other).includes(root),
          );
          for (const other of family) {
            const box = boxes.get(other);
            if (box) boxes.set(other, { ...box, y1: box.y1 + dy, y2: box.y2 + dy });
            const definition = placed.get(other);
            if (definition?.position)
              placed.set(other, {
                ...definition,
                position: { ...definition.position, y: definition.position.y + dy },
              });
            const node = cy.getElementById(other);
            if (node.nonempty() && node.isChildless()) {
              const previous = relocated.get(other) ?? node.position();
              if (!saved.positions.has(other)) saved.positions.set(other, { ...previous });
              relocated.set(other, { x: previous.x, y: previous.y + dy });
            }
          }
          position.y += dy;
        }
      }
    }
    const result = { ...element, position };
    placed.set(id, result);
    boxes.set(id, boxAt(position, half));
    childless.add(id);
    for (const [depth, parentId] of parents.entries()) {
      boxes.set(parentId, grownAt(parentId, depth, position));
      childless.delete(parentId);
    }
    return result;
  };
  const pending = elements.filter((e) => cy.getElementById(e.data.id).empty());
  pending.forEach(place);
  return { additions: pending.map((element) => placed.get(element.data.id) ?? element), relocated };
}
