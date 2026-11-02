import type { ImpactCone } from "@contracts";
import type { State } from "../state";
import type { GraphElement } from "./elements";
import { identityOrder } from "./agentMarks";
import type { MapModel } from "./mapModel";

export const decisionsLabel = (n: number) =>
  n ? `${String(n)} ${n === 1 ? "decision" : "decisions"}` : undefined;
export const testedLabel = (n: number, tests = false) =>
  n && !tests ? `tested by ${String(n)} ${n === 1 ? "call" : "calls"}` : undefined;
export const displayName = (name: string, group: string) =>
  group === "tests" ? { name: name.replace(/^test_/, ""), fullName: name } : { name };

/** Resolve original nodes, including hidden tests and nodes removed by a pending change. */
export function moduleOwners(elements: readonly GraphElement[]) {
  const nodes = new Map(
    elements.filter((e) => e.group === "nodes").map((e) => [e.data.id, e.data]),
  );
  return new Map(
    [...nodes].map(([id, data]) => {
      while (data.parent && nodes.has(data.parent)) data = nodes.get(data.parent) ?? data;
      return [id, data.id];
    }),
  );
}

/** Evidence is projected from original elements and state, never from the folded graph. */
export function mapEvidence(
  model: MapModel,
  elements: readonly GraphElement[],
  state: State,
  cone?: ImpactCone | null,
) {
  const owners = moduleOwners(elements);
  const graphNodes = new Map(state.graph?.nodes.map((n) => [n.id, n]));
  for (const [id, original] of graphNodes) {
    let node = original;
    while (node.parent_id && graphNodes.has(node.parent_id))
      node = graphNodes.get(node.parent_id) ?? node;
    owners.set(id, node.id);
  }
  const seeds = new Set(cone?.seeds.map((id) => owners.get(id)));
  for (const group of model.groups) {
    const ids = new Set(group.modules.map((m) => m.id));
    const belongs = (id: string) => ids.has(owners.get(id) ?? id);
    const marks = elements
      .filter((e) => e.group === "nodes" && belongs(e.data.id))
      .flatMap((e) => e.data.marks?.badges ?? [])
      .sort(identityOrder);
    group.rings = [...new Map(marks.map((mark) => [mark.team, mark])).values()];
    group.decisions = Object.values(state.changes).filter(
      (c) =>
        (c.status === "ready_for_review" || c.status === "conflict") &&
        [...c.graph.nodes_added, ...c.graph.nodes_modified, ...c.graph.nodes_removed].some((node) =>
          belongs(typeof node === "string" ? node : node.id),
        ),
    ).length;
    group.tested =
      state.graph?.edges.filter(
        (e) =>
          e.kind === "calls" &&
          e.confidence === "exact" &&
          graphNodes.get(e.src)?.is_test &&
          belongs(e.dst),
      ).length ?? 0;
    group.coneCount = cone?.entries.filter((e) => belongs(e.node_id)).length;
    group.coneTotal = cone?.entries.length;
    for (const module of group.modules) {
      const entries = cone?.entries.filter((e) => owners.get(e.node_id) === module.id) ?? [];
      module.inCone = cone ? seeds.has(module.id) || entries.length > 0 : undefined;
      module.distance =
        !seeds.has(module.id) && entries.length
          ? Math.min(...entries.map((e) => e.distance))
          : undefined;
    }
  }
  return model;
}
