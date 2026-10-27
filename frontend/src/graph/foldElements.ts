import type { Slot } from "@contracts";

import { getActiveTheme } from "../theme";

import type { GraphElement } from "./elements";
import { foldPositions } from "./foldPositions";
import { nodeGeometry } from "./geometry";
import type { NodeMarks, Word } from "./layers";

const strength: Record<Word, number> = { editing: 3, changed: 2, new: 2, read: 1 };
function combinedMarks(nodes: GraphElement[]): NodeMarks | undefined {
  const marks = nodes.flatMap((n) => (n.data.marks ? [n.data.marks] : []));
  const strongest = [...marks].sort(
    (a, b) => strength[b.word] - strength[a.word] || (a.slot < b.slot ? -1 : 1),
  )[0];
  if (!strongest) return undefined;
  return {
    ...strongest,
    badges: (["A", "B"] as Slot[]).flatMap((slot) => {
      const badges = marks.flatMap((m) => m.badges).filter((b) => b.slot === slot);
      return badges.length ? [{ slot, filled: badges.some((b) => b.filled) }] : [];
    }),
  };
}

/** Folded rows represent descendants' reads, changes, nearest cone distance and merged call edges. */
export function foldElements(elements: GraphElement[], expanded: ReadonlySet<string>) {
  const theme = getActiveTheme();
  const { positions, visible } = foldPositions(elements, expanded, theme);
  const represented = new Map<string, GraphElement[]>();
  for (const node of elements.filter((e) => e.group === "nodes")) {
    const id = visible.get(node.data.id) ?? node.data.id;
    represented.set(id, [...(represented.get(id) ?? []), node]);
  }
  const result: GraphElement[] = [];
  for (const [id, group] of represented) {
    const original = group.find((e) => e.data.id === id);
    if (!original) continue;
    const folded = original.data.kind === "class" && !expanded.has(id);
    const classes = new Set(
      group
        .flatMap((e) => e.classes.split(" "))
        .filter((c) => c !== "dimmed" && c !== "cone" && c !== "marked"),
    );
    // A class's kind remains a class; descendants do not turn it into a method or another frame.
    for (const kind of ["module", "class", "function", "method"]) classes.delete(kind);
    classes.add(original.data.kind);
    if (group.every((e) => e.classes.split(" ").includes("dimmed"))) classes.add("dimmed");
    const distance = Math.min(
      Infinity,
      ...group.flatMap((e) => (e.data.distance === undefined ? [] : [e.data.distance])),
    );
    const marks = combinedMarks(group);
    if (marks) classes.add("marked");
    if (distance !== Infinity) classes.add("cone");
    if (folded) classes.add("folded");
    result.push({
      ...original,
      data: {
        ...original.data,
        ...(folded ? { h: nodeGeometry(theme).headerHeight } : {}),
        ...(marks ? { marks } : {}),
        ...(distance !== Infinity ? { distance } : {}),
      },
      classes: [...classes].join(" "),
      position: original.position ? positions.get(id) : undefined,
    });
  }
  const merged = new Map<string, GraphElement>();
  for (const edge of elements.filter((e) => e.group === "edges")) {
    const source = visible.get(edge.data.source ?? "") ?? edge.data.source;
    const target = visible.get(edge.data.target ?? "") ?? edge.data.target;
    const remapped = source !== edge.data.source || target !== edge.data.target;
    if (remapped && source === target) continue;
    const id = remapped ? `fold:${edge.data.kind}:${source ?? ""}>${target ?? ""}` : edge.data.id;
    const previous = merged.get(id);
    const classes = new Set([...(previous?.classes.split(" ") ?? []), ...edge.classes.split(" ")]);
    if (previous && (!previous.classes.includes("dimmed") || !edge.classes.includes("dimmed")))
      classes.delete("dimmed");
    merged.set(id, {
      ...edge,
      data: { ...edge.data, id, source, target },
      classes: [...classes].join(" "),
    });
  }
  return { elements: [...result, ...merged.values()], visible };
}
