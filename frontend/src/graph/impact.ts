// Live edits have no server proposal yet. Derive their cone from the current change overlay,
// shared by graph dimming and the field; never use an earlier proposal as a live preview.
import type { GraphEdge, GraphNode, ImpactCone } from "@contracts";
import type { ChangeState, State } from "../state";

const constructors = new Set(["__init__", "__new__", "__post_init__"]);

function constructedClass(nodes: ReadonlyMap<string, GraphNode>, id: string): string | null {
  const node = nodes.get(id);
  if (node?.kind !== "method" || !constructors.has(node.name) || !node.parent_id) return null;
  return nodes.get(node.parent_id)?.kind === "class" ? node.parent_id : null;
}

/** Exact reverse calls, including cycles and constructor/class bridges, in breadth-first order. */
function distances(
  nodes: ReadonlyMap<string, GraphNode>,
  callers: ReadonlyMap<string, Set<string>>,
  seeds: string[],
) {
  const distance = new Map(seeds.map((id) => [id, 0]));
  let layer = seeds;
  let depth = 0;
  while (layer.length > 0) {
    depth += 1;
    const found: string[] = [];
    const add = (id: string) => {
      if (!distance.has(id)) {
        distance.set(id, depth);
        found.push(id);
      }
    };
    for (const callee of layer) for (const caller of callers.get(callee) ?? []) add(caller);
    for (const id of [...found]) {
      const parent = constructedClass(nodes, id);
      if (parent) add(parent);
    }
    layer = found;
  }
  return distance;
}

/** Main with this change applied, shared by both impact calculations. */
function impactGraph(state: State, change: ChangeState) {
  if (!state.graph) return null;
  const removed = new Set(change.graph.nodes_removed);
  const nodes = new Map(
    [...state.graph.nodes, ...change.graph.nodes_added]
      .filter((node) => !removed.has(node.id))
      .map((node) => [node.id, node]),
  );
  const edges = new Map(state.graph.edges.map((edge) => [edge.id, edge]));
  for (const id of change.graph.edges_removed) edges.delete(id);
  for (const edge of change.graph.edges_added) edges.set(edge.id, edge);
  return { nodes, edges };
}

const exactConfidence: Record<GraphEdge["confidence"], boolean> = { exact: true, possible: false };

/** Distinct non-test callers outside the exact cone, with no exact call into it. */
export function possibleCallerNodes(
  state: State,
  change: ChangeState,
  cone: ImpactCone,
): GraphNode[] | null {
  const graph = impactGraph(state, change);
  if (!graph) return null;
  const { nodes, edges } = graph;
  const targets = new Set([...cone.seeds, ...cone.entries.map((entry) => entry.node_id)]);
  const possible = new Set<string>();
  const exact = new Set<string>();
  for (const edge of edges.values()) {
    const source = nodes.get(edge.src);
    if (
      edge.kind !== "calls" ||
      !nodes.has(edge.dst) ||
      !targets.has(edge.dst) ||
      !source ||
      source.is_test ||
      targets.has(edge.src)
    )
      continue;
    (exactConfidence[edge.confidence] ? exact : possible).add(edge.src);
  }
  return [...nodes.values()].filter((node) => possible.has(node.id) && !exact.has(node.id));
}

/** The same run limits as the backend; this pure preview makes no request. */
export function liveImpact(state: State, change: ChangeState): ImpactCone | null {
  const graph = impactGraph(state, change);
  if (!graph || !state.run) return null;
  const { nodes, edges } = graph;
  const calls = [...edges.values()].filter(
    (edge) =>
      edge.kind === "calls" &&
      edge.confidence === "exact" &&
      nodes.has(edge.src) &&
      nodes.has(edge.dst),
  );
  const callers = new Map<string, Set<string>>();
  for (const edge of calls) {
    const ids = callers.get(edge.dst) ?? new Set<string>();
    ids.add(edge.src);
    callers.set(edge.dst, ids);
  }
  const seeds = new Set(
    [...change.graph.nodes_added.map((node) => node.id), ...change.graph.nodes_modified].filter(
      (id) => nodes.has(id),
    ),
  );
  for (const id of [...seeds]) {
    const parent = constructedClass(nodes, id);
    if (parent) seeds.add(parent);
  }
  const orderedSeeds = [...seeds].sort();
  const distance = distances(nodes, callers, orderedSeeds);
  const reached = [...distance]
    .filter(([id]) => !seeds.has(id) && !nodes.get(id)?.is_test)
    .map(([node_id, depth]) => ({ node_id, distance: depth }))
    .sort((a, b) => a.distance - b.distance || a.node_id.localeCompare(b.node_id));
  const { cone_max_depth: max_depth, cone_max_nodes: max_nodes } = state.run.limits;
  const within = reached.filter((entry) => entry.distance <= max_depth);
  const tests = new Set(
    calls
      .filter((edge) => {
        const node = nodes.get(edge.src);
        return (
          node?.is_test &&
          (node.kind === "function" || node.kind === "method") &&
          node.name.startsWith("test") &&
          distance.has(edge.dst)
        );
      })
      .map((edge) => edge.src),
  );
  return {
    seeds: orderedSeeds,
    entries: within.slice(0, max_nodes),
    edges_followed: "exact",
    max_depth,
    max_nodes,
    omitted_by_depth: reached.length - within.length,
    omitted_by_limit: Math.max(0, within.length - max_nodes),
    tests_reaching: tests.size,
  };
}

/** Count and listing deliberately share the same caller definition. */
export const possibleCallers = (state: State, change: ChangeState, cone: ImpactCone) =>
  possibleCallerNodes(state, change, cone)?.length ?? null;
