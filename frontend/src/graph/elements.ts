import type { GraphEdge, GraphNode, Point } from "@contracts";

import { selectNodeMarks, selectPendingChanges } from "../state";
import type { State } from "../state";

export interface GraphElement {
  group: "nodes" | "edges";
  data: {
    id: string;
    kind: GraphNode["kind"] | GraphEdge["kind"];
    label?: string;
    parent?: string;
    source?: string;
    target?: string;
  };
  classes: string;
  position?: Point;
}

/** Main stays intact; unapproved additions and marks are a separate visual overlay. */
export function graphElements(state: State, showTests = false): GraphElement[] {
  const nodes = new Map(state.graph?.nodes.map((node) => [node.id, node]));
  const edges = new Map(state.graph?.edges.map((edge) => [edge.id, edge]));
  const nodeMarks = selectNodeMarks(state);
  for (const change of selectPendingChanges(state)) {
    for (const node of change.graph.nodes_added) {
      if (!nodes.has(node.id)) nodes.set(node.id, node);
    }
    for (const edge of change.graph.edges_added) {
      if (!edges.has(edge.id)) edges.set(edge.id, edge);
    }
  }
  const visible = new Map([...nodes].filter(([, node]) => showTests || !node.is_test));
  const elements: GraphElement[] = [];
  for (const node of visible.values()) {
    const classes: string[] = [node.kind];
    const marks = nodeMarks[node.id];
    const badges: string[] = [];
    for (const [slot, mode] of Object.entries(marks?.focus ?? {})) {
      classes.push(`focus-${slot}-${mode}`);
      badges.push(`${slot} ${mode}`);
    }
    if (marks?.focus.A && marks.focus.B) classes.push("focus-both");
    if (marks?.pending) {
      classes.push("pending", ...marks.pendingSlots.map((slot) => `pending-${slot}`));
      if (marks.pendingSlots.length === 2) classes.push("pending-both");
      badges.push(`${marks.pendingSlots.join("+") || "?"} pending`);
    }
    const position = state.graph?.positions[node.id];
    elements.push({
      group: "nodes",
      data: {
        id: node.id,
        kind: node.kind,
        label: badges.length ? `${node.name}\n${badges.join(" · ")}` : node.name,
        ...(node.parent_id && visible.has(node.parent_id) ? { parent: node.parent_id } : {}),
      },
      classes: classes.join(" "),
      position: position ? { ...position } : undefined,
    });
  }
  for (const edge of edges.values()) {
    if (!visible.has(edge.src) || !visible.has(edge.dst)) continue;
    elements.push({
      group: "edges",
      data: { id: edge.id, kind: edge.kind, source: edge.src, target: edge.dst },
      classes: `${edge.kind} ${edge.confidence}`,
    });
  }
  return elements;
}
