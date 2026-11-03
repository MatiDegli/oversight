import type { GraphEdge, GraphNode, ImpactCone, Point } from "@contracts";

import { selectPendingChanges } from "../state";
import type { State } from "../state";
import { getActiveTheme } from "../theme";

import { nodeGeometry, rowWidthAt } from "./geometry";
import type { NodeMarks } from "./layers";
import type { AgentMark } from "./agentMarks";
import { combineMarks, selectAgentMarks, marksPaint, identityOrder } from "./agentMarks";
import type { PaletteKey } from "../theme";
import { presentationFor } from "../teams/presentation";
import { folderGroups } from "./groups";
import { BUILTIN_NAMES } from "./builtinNames";

export type MeasuredWord = "differs" | "same" | "varied" | "not measured";

export interface GraphElement {
  group: "nodes" | "edges";
  data: {
    id: string;
    kind: GraphNode["kind"] | GraphEdge["kind"];
    group?: string;
    label?: string;
    callCount?: number;
    exactCount?: number;
    possibleCount?: number;
    restPossible?: boolean;
    /** A frame's header: its title (a module's file name) and its count, drawn in place of a label. */
    title?: string;
    count?: string;
    /** The outer size of a row, or of a frame with nothing in it; a frame with children has its own. */
    w?: number;
    h?: number;
    /** The state word and letter badges of a leaf an agent touches. */
    marks?: NodeMarks;
    paint?: PaletteKey;
    pendingAgents?: string;
    distance?: number;
    measured?: MeasuredWord;
    parent?: string;
    source?: string;
    target?: string;
  };
  classes: string;
  position?: Point;
}

const plural = (count: number, one: string, many: string) =>
  `${String(count)} ${count === 1 ? one : many}`;

/** A frame's count: its functions or methods, then the classes if it has no functions, and what is new. */
function countOf(node: GraphNode, below: { callables: number; classes: number; added: number }) {
  const unit =
    node.kind === "class"
      ? plural(below.callables, "method", "methods")
      : below.callables > 0 || below.classes === 0
        ? plural(below.callables, "function", "functions")
        : plural(below.classes, "class", "classes");
  return below.added > 0 ? `${unit} · ${String(below.added)} new` : unit;
}

/**
 * Main stays intact; unapproved additions and marks are a separate visual overlay. With `lit`, every
 * node outside it dims. Compound frames dim their paint rather than their subtree, so their
 * children retain independent opacity. Edges with both ends lit keep full opacity.
 */
export function graphElements(
  state: State,
  showTests = false,
  lit: ReadonlySet<string> | null = null,
  cone: ImpactCone | null = null,
  preserveReads = false,
  measured: ReadonlyMap<string, MeasuredWord | null> | null = null,
  merging: readonly string[] = [],
): GraphElement[] {
  const nodes = new Map(state.graph?.nodes.map((node) => [node.id, node]));
  const edges = new Map(state.graph?.edges.map((edge) => [edge.id, edge]));
  const nodeMarks = selectAgentMarks(state, merging);
  const addedNodeSlots = new Map<string, string[]>();
  for (const id of merging) {
    const change = state.changes[id];
    if (!change || change.resolution?.merge !== "merged") continue;
    for (const node of change.graph.nodes_added) {
      if (!nodes.has(node.id)) nodes.set(node.id, node);
      addedNodeSlots.set(node.id, ["pending-new"]);
    }
  }
  const addedEdgeSlots = new Map<string, string[]>();
  const edgeMarks = new Map<string, AgentMark[]>();
  for (const change of selectPendingChanges(state)) {
    const identity = presentationFor(state, change.agent_id);
    for (const node of change.graph.nodes_added) {
      if (!nodes.has(node.id)) nodes.set(node.id, node);
      addedNodeSlots.set(node.id, ["pending-new"]);
    }
    for (const edge of change.graph.edges_added) {
      if (!edges.has(edge.id)) edges.set(edge.id, edge);
      addedEdgeSlots.set(edge.id, ["pending"]);
      if (identity)
        edgeMarks.set(edge.id, [
          ...(edgeMarks.get(edge.id) ?? []),
          { ...identity, word: "changed", pending: true, filled: true },
        ]);
    }
  }
  const visible = new Map([...nodes].filter(([, node]) => showTests || !node.is_test));
  const groups = new Map(
    folderGroups([...visible.values()]).flatMap((group) =>
      group.moduleIds.map((id) => [id, group] as const),
    ),
  );
  const distances = new Map(cone?.entries.map((entry) => [entry.node_id, entry.distance]));
  const coneIds = new Set([...(cone?.seeds ?? []), ...(cone?.entries.map((e) => e.node_id) ?? [])]);
  const dims = (id: string) => lit !== null && !lit.has(id);
  // What each frame holds, for its count: the visible functions and methods below it, its classes
  // and the nodes a change adds.
  const below = new Map<
    string,
    { callables: number; classes: number; added: number; children: number }
  >();
  const classDepth = (node: GraphNode): number => {
    let depth = 0;
    let id: string | null | undefined = node.parent_id;
    while (id && visible.has(id)) {
      if (visible.get(id)?.kind === "class") depth += 1;
      id = visible.get(id)?.parent_id;
    }
    return depth;
  };
  const theme = getActiveTheme();
  const geometry = nodeGeometry(theme);
  for (const node of visible.values()) {
    if (node.parent_id && visible.has(node.parent_id)) {
      const parent = below.get(node.parent_id) ?? {
        callables: 0,
        classes: 0,
        added: 0,
        children: 0,
      };
      parent.children += 1;
      below.set(node.parent_id, parent);
    }
    for (
      let id: string | null | undefined = node.parent_id;
      id && visible.has(id);
      id = visible.get(id)?.parent_id
    ) {
      const entry = below.get(id) ?? { callables: 0, classes: 0, added: 0, children: 0 };
      if (node.kind === "function" || node.kind === "method") {
        entry.callables += 1;
        if (addedNodeSlots.has(node.id)) entry.added += 1;
      }
      if (node.kind === "class" && node.parent_id === id) entry.classes += 1;
      below.set(id, entry);
    }
  }
  const elements: GraphElement[] = [];
  for (const node of visible.values()) {
    const classes: string[] = [node.kind, ...(addedNodeSlots.get(node.id) ?? [])];
    if (dims(node.id)) classes.push("dimmed");
    const distance = lit?.has(node.id) ? distances.get(node.id) : undefined;
    if (distance !== undefined) classes.push("cone");
    const word =
      distance !== undefined &&
      !cone?.seeds.includes(node.id) &&
      !nodeMarks[node.id]?.some((mark) => mark.pending)
        ? measured?.get(node.id)
        : null;
    if (word) classes.push(`measured-${word === "not measured" ? "unmeasured" : word}`);
    const touch = nodeMarks[node.id] ?? [];
    const pending = touch.some((mark) => mark.pending);
    // Review history cannot paint work outside a pending change.
    const shown = lit !== null && !preserveReads && !pending ? [] : touch;
    if (shown.some((mark) => mark.word === "read")) classes.push("focus-read");
    if (shown.some((mark) => mark.word === "editing")) classes.push("focus-edit");
    if (pending) classes.push("pending");
    const marks = combineMarks(shown);
    if (marks) classes.push("marked");
    const frame = node.kind === "module" || node.kind === "class";
    const held = below.get(node.id) ?? { callables: 0, classes: 0, added: 0, children: 0 };
    // A row is as wide as the frame it sits in leaves room for; a module with nothing in it is a
    // frame's width, and a class with nothing in it the width a row would have there.
    const size = !frame
      ? { w: rowWidthAt(theme, classDepth(node)), h: geometry.rowHeight }
      : held.children > 0
        ? {}
        : {
            w:
              node.kind === "module"
                ? geometry.outer.module.left + geometry.rowWidth + geometry.outer.module.right
                : rowWidthAt(theme, classDepth(node)),
            h: geometry.headerHeight,
          };
    const position = state.graph?.positions[node.id];
    elements.push({
      group: "nodes",
      data: {
        id: node.id,
        kind: node.kind,
        ...(groups.has(node.id) ? { group: groups.get(node.id)?.id } : {}),
        ...(distance !== undefined ? { distance } : {}),
        ...(word ? { measured: word } : {}),
        label: node.name,
        ...size,
        ...(marks ? { marks, ...marksPaint(marks) } : {}),
        ...(frame
          ? {
              title: node.kind === "module" ? (node.path.split("/").pop() ?? node.name) : node.name,
              count: countOf(node, held),
            }
          : {}),
        ...(node.parent_id && visible.has(node.parent_id) ? { parent: node.parent_id } : {}),
      },
      classes: classes.join(" "),
      position: position ? { ...position } : undefined,
    });
  }
  for (const edge of edges.values()) {
    const marks = combineMarks((edgeMarks.get(edge.id) ?? []).sort(identityOrder));
    if (!visible.has(edge.src) || !visible.has(edge.dst)) continue;
    elements.push({
      group: "edges",
      data: {
        id: edge.id,
        kind: edge.kind,
        source: edge.src,
        target: edge.dst,
        ...(marks ? { marks, ...marksPaint(marks) } : {}),
        restPossible: !BUILTIN_NAMES.has(nodes.get(edge.dst)?.name ?? ""),
      },
      classes: [
        edge.kind,
        edge.confidence,
        ...(addedEdgeSlots.get(edge.id) ?? []),
        // In watch, an edge stays quiet unless it touches a node an agent reads, edits or has
        // changed (or one the person selects, which the selection adds), or is itself a change.
        ...(lit === null &&
        !addedEdgeSlots.has(edge.id) &&
        !nodeMarks[edge.src] &&
        !nodeMarks[edge.dst]
          ? ["quiet"]
          : []),
        ...(lit === null
          ? []
          : lit.has(edge.src) && lit.has(edge.dst)
            ? edge.confidence === "exact"
              ? ["lit"]
              : coneIds.has(edge.src) && coneIds.has(edge.dst)
                ? ["cone-call"]
                : []
            : ["dimmed"]),
      ].join(" "),
    });
  }
  return elements;
}
