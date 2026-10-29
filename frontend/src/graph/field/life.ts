// What the life layer reads from the state: the merges that light their node and send a pulse, and
// the modules the rest contours rise over. Pure selectors of the reduced state, shared by live runs
// and replay, so nothing here depends on the reader's clock beyond the event times.
import type { GraphSnapshot } from "@contracts";

import type { State } from "../../state";
import { agentLook } from "../../theme";
import type { FieldMerge, FieldModule } from "./lights";

/**
 * The merges that landed: one per merged change, at its first modified or added node, as old as
 * the time between its decision and the latest event the state has applied. Rejected, conflicting
 * and waiting changes have none.
 */
export function selectMerges(state: State): FieldMerge[] {
  const now = state.clock === null ? Number.NaN : Date.parse(state.clock);
  const merges: FieldMerge[] = [];
  for (const change of Object.values(state.changes)) {
    if (change.resolution?.merge !== "merged") continue;
    const agent = state.agents[change.agent_id];
    const id = change.graph.nodes_modified[0] ?? change.graph.nodes_added[0]?.id;
    if (!agent || id === undefined) continue;
    const decided = Date.parse(change.resolutionEvent?.ts ?? "");
    merges.push({
      key: `${change.change_id}:${String(change.resolutionEvent?.seq ?? change.resolutionSeq ?? 0)}`,
      slot: agent.slot,
      source: agentLook[agent.slot].family,
      id,
      age: Number.isFinite(now) && Number.isFinite(decided) ? Math.max(0, now - decided) : 0,
    });
  }
  return merges.sort((a, b) => a.key.localeCompare(b.key));
}

const modulesOf = new WeakMap<GraphSnapshot, FieldModule[]>();
const NONE: FieldModule[] = [];

/**
 * The modules of main's graph, test modules left out, each with the number of call edges that
 * reach into it from another module: what makes its hill a little taller.
 */
export function selectModules(graph: GraphSnapshot | null): FieldModule[] {
  if (!graph) return NONE;
  const known = modulesOf.get(graph);
  if (known) return known;
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
  const moduleOf = (id: string): string | null => {
    let node = nodes.get(id);
    // The parents of a method are a class and a module: a few hops, bounded against bad data.
    for (let hops = 0; node && hops < 8; hops++) {
      if (node.kind === "module") return node.id;
      node = node.parent_id === null ? undefined : nodes.get(node.parent_id);
    }
    return null;
  };
  const callers = new Map<string, number>();
  for (const edge of graph.edges) {
    if (edge.kind !== "calls") continue;
    const to = moduleOf(edge.dst);
    const from = moduleOf(edge.src);
    if (to !== null && from !== null && to !== from) callers.set(to, (callers.get(to) ?? 0) + 1);
  }
  const modules = graph.nodes
    .filter((node) => node.kind === "module" && !node.is_test)
    .map((node) => ({ id: node.id, callers: callers.get(node.id) ?? 0 }));
  modulesOf.set(graph, modules);
  return modules;
}
