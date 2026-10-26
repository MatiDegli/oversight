import type { GraphEdge, GraphNode, Point, Slot } from "@contracts";

import { selectPendingChanges } from "../state";
import type { State } from "../state";
import { agentColors } from "../theme";

export interface GraphElement {
  group: "nodes" | "edges";
  data: {
    id: string;
    kind: GraphNode["kind"] | GraphEdge["kind"];
    label?: string;
    parent?: string;
    source?: string;
    target?: string;
    pendingColor?: string;
    aSize?: number;
    bSize?: number;
    aOpacity?: number;
    bOpacity?: number;
  };
  classes: string;
  position?: Point;
}

/** Main stays intact; unapproved additions and marks are a separate visual overlay. */
export function graphElements(state: State, showTests = false): GraphElement[] {
  const nodes = new Map(state.graph?.nodes.map((node) => [node.id, node]));
  const edges = new Map(state.graph?.edges.map((edge) => [edge.id, edge]));
  const pending = new Map<string, Slot[]>();
  for (const change of selectPendingChanges(state)) {
    const slot = state.agents[change.agent_id]?.slot;
    for (const node of change.graph.nodes_added) {
      if (!nodes.has(node.id)) nodes.set(node.id, node);
    }
    for (const edge of change.graph.edges_added) {
      if (!edges.has(edge.id)) edges.set(edge.id, edge);
    }
    if (!slot) continue;
    for (const id of [
      ...change.graph.nodes_added.map((node) => node.id),
      ...change.graph.nodes_modified,
      ...change.graph.nodes_removed,
    ])
      pending.set(id, [...new Set([...(pending.get(id) ?? []), slot])].sort());
  }
  const visible = new Map([...nodes].filter(([, node]) => showTests || !node.is_test));
  const elements: GraphElement[] = [];
  for (const node of visible.values()) {
    const classes: string[] = [node.kind];
    const marks: string[] = [];
    const focus: Partial<Record<Slot, "read" | "edit">> = {};
    for (const agentFocus of Object.values(state.focus)) {
      const agent = state.agents[agentFocus.agent_id];
      if (!agent || (agent.state !== "working" && agent.state !== "revising")) continue;
      if (!agentFocus.node_ids.includes(node.id)) continue;
      focus[agent.slot] = agentFocus.mode;
      classes.push(`focus-${agent.slot}-${agentFocus.mode}`);
      marks.push(`${agent.slot} ${agentFocus.mode}`);
    }
    const slots = pending.get(node.id) ?? [];
    for (const slot of slots) marks.push(`${slot} pending`);
    if (slots.length) classes.push("pending", ...slots.map((slot) => `pending-${slot}`));
    const count = Object.keys(focus).length;
    const position = state.graph?.positions[node.id];
    elements.push({
      group: "nodes",
      data: {
        id: node.id,
        kind: node.kind,
        label: [node.name, ...marks].join("\n"),
        ...(node.parent_id && visible.has(node.parent_id) ? { parent: node.parent_id } : {}),
        pendingColor: slots[0] ? agentColors[slots[0]] : agentColors.A,
        aSize: focus.A ? 100 / count : 0,
        bSize: focus.B ? 100 / count : 0,
        aOpacity: focus.A === "edit" ? 0.75 : 0.3,
        bOpacity: focus.B === "edit" ? 0.75 : 0.3,
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
