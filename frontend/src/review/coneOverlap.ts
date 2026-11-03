// eslint-disable-next-line no-restricted-imports -- Reuse the pure cone calculation without loading the graph renderer in Chat.
import { liveImpact } from "../graph/impact";
import { selectPendingChanges, type State } from "../state";

export type ConeLimitation =
  "parse-errors" | "sequence-gap" | "cone-omissions" | "missing-seeds" | "removed-only";
export interface ConeIntersection {
  changeId: string;
  agentId: string;
  nodes: { id: string; name: string }[];
  unavailable: "graph" | "run limits" | null;
  limitations: ConeLimitation[];
}

/** Current graph membership only; never a conflict, behavior claim or approval input. */
export function coneOverlap(state: State, changeId: string): ConeIntersection[] {
  const reviewed = state.changes[changeId];
  if (!reviewed) return [];
  const edits = [
    ...new Set([
      ...reviewed.graph.nodes_added.map((node) => node.id),
      ...reviewed.graph.nodes_modified,
      ...reviewed.graph.nodes_removed,
    ]),
  ].sort();
  return selectPendingChanges(state)
    .filter((peer) => peer.change_id !== changeId && peer.agent_id !== reviewed.agent_id)
    .sort((a, b) => a.change_id.localeCompare(b.change_id))
    .map((peer): ConeIntersection => {
      const cone = liveImpact(state, peer);
      const membership = new Set([
        ...(cone?.seeds ?? []),
        ...(cone?.entries.map((entry) => entry.node_id) ?? []),
      ]);
      const names = new Map(
        [
          ...(state.graph?.nodes ?? []),
          ...peer.graph.nodes_added,
          ...reviewed.graph.nodes_added,
        ].map((node) => [node.id, node.qualname]),
      );
      const removed = new Set(peer.graph.nodes_removed);
      const seeds = [
        ...peer.graph.nodes_added.map((node) => node.id),
        ...peer.graph.nodes_modified,
      ].filter((id) => !removed.has(id));
      const available = new Set([
        ...(state.graph?.nodes.map((node) => node.id) ?? []),
        ...peer.graph.nodes_added.map((node) => node.id),
      ]);
      const limitations: ConeLimitation[] = [];
      if (state.graph?.parse_errors.length) limitations.push("parse-errors");
      if (state.gap) limitations.push("sequence-gap");
      if (cone && (cone.omitted_by_depth || cone.omitted_by_limit))
        limitations.push("cone-omissions");
      if (state.graph && seeds.some((id) => !available.has(id))) limitations.push("missing-seeds");
      if (!seeds.length && removed.size) limitations.push("removed-only");
      return {
        changeId: peer.change_id,
        agentId: peer.agent_id,
        nodes: edits
          .filter((id) => membership.has(id))
          .map((id) => ({ id, name: names.get(id) || id })),
        unavailable: !state.graph ? "graph" : !state.run ? "run limits" : null,
        limitations,
      };
    });
}
