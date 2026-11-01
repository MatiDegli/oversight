// What the life layer reads from the state: the merges that light their node and send a pulse, and
// the modules the rest contours rise over. Pure selectors of the reduced state, shared by live runs
// and replay, so nothing here depends on the reader's clock beyond the event times.
import type { GraphSnapshot } from "@contracts";

import { parseNeedsYou, type State } from "../../state";
import type { Milestone } from "./milestones";
import { agentLook } from "../../theme";
import type { FieldMerge, FieldModule } from "./lights";

/** The node a change's light sits on: its first modified or added node. */
export function changeLightNode(change: State["changes"][string]): string | undefined {
  return change.graph.nodes_modified[0] ?? change.graph.nodes_added[0]?.id;
}

/**
 * The decided changes whose light fades: one per merged change (with a pulse) and one per rejected
 * change (without), at the change's node, as old as the time between its decision and the latest
 * event the state has applied. Waiting and conflicting changes have none: their light is steady
 * (`selectSignalLights`) until the decision closes their inbox entries.
 */
export function selectMerges(state: State): FieldMerge[] {
  const now = state.clock === null ? Number.NaN : Date.parse(state.clock);
  const merges: FieldMerge[] = [];
  for (const change of Object.values(state.changes)) {
    const merged = change.resolution?.merge === "merged";
    if (!merged && change.resolution?.decision !== "rejected") continue;
    const agent = state.agents[change.agent_id];
    const id = changeLightNode(change);
    if (!agent || id === undefined) continue;
    const decided = Date.parse(change.resolutionEvent?.ts ?? "");
    merges.push({
      key: `${change.change_id}:${String(change.resolutionEvent?.seq ?? change.resolutionSeq ?? 0)}`,
      slot: agent.slot,
      source: agentLook[agent.slot].family,
      id,
      pulse: merged,
      age: Number.isFinite(now) && Number.isFinite(decided) ? Math.max(0, now - decided) : 0,
    });
  }
  return merges.sort((a, b) => a.key.localeCompare(b.key));
}

/** Milestones keep their event identity and age; a missing change node lights nothing. */
export function selectMilestones(state: State): Milestone[] {
  const now = Date.parse(state.clock ?? "");
  const result: Milestone[] = [];
  for (const event of state.decisionEvents) {
    let kind: Milestone["kind"];
    let changeId: string | null | undefined;
    if (event.type === "agent.status" && event.payload.state === "ready_for_review") {
      kind = "ready";
      changeId = event.payload.change_id;
    } else if (event.type === "error" && parseNeedsYou(event.payload.message)) {
      kind = "needs";
      changeId = event.payload.change_id;
    } else continue;
    const change = changeId ? state.changes[changeId] : undefined;
    const agent = change && state.agents[change.agent_id];
    const id = change && changeLightNode(change);
    if (!agent || !id) continue;
    const age = now - Date.parse(event.ts);
    result.push({
      key: `${String(changeId)}:${String(event.seq)}`,
      kind,
      slot: agent.slot,
      source: agentLook[agent.slot].family,
      id,
      age: Number.isFinite(age) ? Math.max(0, age) : 0,
    });
  }
  return [
    ...result,
    ...selectMerges(state)
      .filter((merge) => merge.pulse)
      .map((merge) => ({ ...merge, kind: "merged" as const })),
  ];
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
