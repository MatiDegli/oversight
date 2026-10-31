import type { State } from "../state";
import { selectGraphBehavior, selectGraphBehaviorNode } from "./behavior";

/** Direct behavior evidence only; measured callers never create chips. */
export function selectNodeChips(state: State, litIds: readonly string[] | null) {
  const entries = selectGraphBehavior(state).filter(
    (entry) => litIds === null || litIds.includes(entry.nodeId),
  );
  return entries.flatMap((entry) => {
    const node = selectGraphBehaviorNode(state, entry.changeId, entry.nodeId);
    if (!node || entry.compared === 0) return [];
    const rows = node.rows.slice(0, 8);
    const varied = rows.filter((row) => row.unstable).length;
    if (entry.differing === 0 && !entry.unstable) return [];
    const kind = entry.differing > 0 ? "differing" : "varied";
    const text =
      kind === "differing"
        ? `${String(entry.differing)} of ${String(entry.compared)} differ`
        : `varied on ${String(varied)} of ${String(entry.compared)} inputs`;
    const letter =
      entries.filter(
        (other) =>
          other.nodeId === entry.nodeId &&
          other.compared > 0 &&
          (other.differing > 0 || other.unstable),
      ).length > 1
        ? (state.agents[entry.agentId]?.slot ?? entry.agentId.slice(0, 1))
        : null;
    const shown = rows.filter((row) => !row.unstable).length;
    const proposal = state.changes[entry.changeId]?.proposal;
    return [
      {
        ...entry,
        kind,
        rows,
        label: node.label,
        letter,
        text: letter ? `${letter} ${text}` : text,
        key: JSON.stringify([
          entry.changeId,
          entry.nodeId,
          proposal?.revision,
          proposal?.head_commit,
        ]),
        header:
          kind === "differing"
            ? `${String(entry.differing)} of ${String(entry.compared)} inputs differ`
            : text,
        footer:
          shown < entry.differing
            ? `${String(shown)} of ${String(entry.differing)} differing inputs shown`
            : null,
      },
    ];
  });
}
export type NodeChip = ReturnType<typeof selectNodeChips>[number];
