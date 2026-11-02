import type { State } from "../state";
import { selectPendingChanges } from "../state";
import { selectPresentation } from "../teams/presentation";
import type { AgentPresentation } from "../teams/presentation";
import type { NodeMarks, Word } from "./layers";

export type AgentMark = AgentPresentation & {
  word: Word;
  pending: boolean;
  filled: boolean;
  work?: "read" | "edit";
  ready?: boolean;
};
export const markStrength: Record<Word, number> = { editing: 3, changed: 2, new: 2, read: 1 };
export const identityOrder = (a: AgentPresentation, b: AgentPresentation) =>
  a.order - b.order ||
  (a.planId ?? "").localeCompare(b.planId ?? "") ||
  (a.subtask ?? 0) - (b.subtask ?? 0) ||
  a.agentId.localeCompare(b.agentId);

/** Each ownership mark survives palette and capacity reuse, including historical agents. */
export function combineMarks(marks: readonly AgentMark[]): NodeMarks | undefined {
  const byAgent = new Map<string, AgentMark>();
  for (const mark of marks) {
    const before = byAgent.get(mark.agentId);
    const strongest = before && markStrength[before.word] > markStrength[mark.word] ? before : mark;
    byAgent.set(mark.agentId, {
      ...strongest,
      pending: mark.pending || !!before?.pending,
      filled: mark.filled || !!before?.filled,
      work: mark.work ?? before?.work,
      ready: !!mark.ready || !!before?.ready,
    });
  }
  const badges = [...byAgent.values()].sort(identityOrder);
  const owner = [...badges].sort(
    (a, b) => markStrength[b.word] - markStrength[a.word] || identityOrder(a, b),
  )[0];
  return owner ? { word: owner.word, owner, badges } : undefined;
}

/** Pure graph-local facts; the state's capacity-keyed projection remains untouched. */
export function selectAgentMarks(state: State, merging: readonly string[] = []) {
  const identities = selectPresentation(state).byAgent;
  const byNode: Record<string, AgentMark[]> = {};
  const add = (id: string, mark: AgentMark) => {
    (byNode[id] ??= []).push(mark);
  };
  const changes = [
    ...selectPendingChanges(state),
    ...merging.flatMap((id) => state.changes[id] ?? []),
  ];
  for (const change of changes) {
    const identity = identities[change.agent_id];
    if (!identity || (identity.kind === "lead" && identity.agentId === identity.team)) continue;
    const ready = change.status === "ready_for_review" || change.status === "conflict";
    for (const id of [...change.graph.nodes_modified, ...change.graph.nodes_removed])
      add(id, { ...identity, word: "changed", pending: true, filled: true, ready });
    for (const node of change.graph.nodes_added)
      add(node.id, { ...identity, word: "new", pending: true, filled: true, ready });
  }
  for (const focus of Object.values(state.focus)) {
    const agent = state.agents[focus.agent_id],
      identity = identities[focus.agent_id];
    if (
      !agent ||
      !identity ||
      !["working", "revising"].includes(agent.state) ||
      (identity.kind === "lead" && identity.agentId === identity.team)
    )
      continue;
    for (const id of focus.node_ids)
      add(id, {
        ...identity,
        word: focus.mode === "edit" ? "editing" : "read",
        pending: false,
        filled: focus.mode === "edit",
        work: focus.mode,
      });
  }
  return byNode;
}

export function marksPaint(marks: NodeMarks | undefined) {
  if (!marks) return {};
  const pending = marks.badges.find((mark) => mark.pending);
  return {
    paint: (pending ?? marks.owner).hue,
    pendingAgents: `|${marks.badges
      .filter((mark) => mark.pending)
      .map((mark) => mark.agentId)
      .join("|")}|`,
  };
}
