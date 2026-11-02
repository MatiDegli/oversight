import { presentationFor, type AgentPresentation } from "../../teams/presentation";
import type { ChangeProposed, ServerEvent } from "@contracts";
import { applyEvent, createInitialState, selectPendingChanges } from "../../state";
import type { State } from "../../state";
import { viewReducer } from "../view";
import type { FocusTab, View } from "../view";

export interface ReplayMark {
  id: string;
  offset: number;
  letters: string;
  presentation?: AgentPresentation;
  kind: "start" | "proposal" | "merge";
  title: string;
  proposal?: ChangeProposed;
  hash?: string;
}
export const replayTime = (ms: number) =>
  `${String(Math.floor(ms / 60000))}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;

/** Include future milestones; one start groups the first status of each recorded agent. */
export function replayMarks(
  events: readonly ServerEvent[],
  offsets: readonly number[],
): ReplayMark[] {
  const agents = new Set<string>();
  let state = createInitialState();
  const marks: ReplayMark[] = [];
  for (const [index, event] of events.entries()) {
    state = applyEvent(state, event);
    const offset = offsets[index] ?? 0;
    const identity =
      "agent_id" in event.payload ? presentationFor(state, event.payload.agent_id) : undefined;
    if (event.type === "agent.status" && !agents.has(event.payload.agent_id)) {
      agents.add(event.payload.agent_id);
      const start = marks.find((mark) => mark.kind === "start");
      if (start) start.letters += `, ${identity?.label ?? "?"}`;
      else
        marks.push({
          id: "start",
          offset,
          letters: identity?.label ?? "?",
          kind: "start",
          title: "Agents started",
        });
    }
    if (event.type !== "change.proposed" && event.type !== "change.resolved") continue;
    const common = {
      id: String(event.seq),
      offset,
      letters: identity?.label ?? "?",
      presentation: identity,
    };
    if (event.type === "change.proposed")
      marks.push({
        ...common,
        kind: "proposal",
        title: `Agent ${identity?.label ?? "unknown"} · Ready for review`,
        proposal: event.payload,
      });
    else if (event.payload.decision === "approved" && event.payload.merge === "merged")
      marks.push({
        ...common,
        kind: "merge",
        title: `Agent ${identity?.label ?? "unknown"} · Merged`,
        hash: event.payload.merge_commit ?? undefined,
      });
  }
  return marks;
}

/** Close through navigation's history rules, without opening or activating a new subject. */
export function reconcileReplayView(view: View, state: State): View {
  const nodes = new Set(
    [
      ...(state.graph?.nodes ?? []),
      ...selectPendingChanges(state).flatMap((change) => change.graph.nodes_added),
    ].map((node) => node.id),
  );
  const hasNode = (id: string | null) => id === null || nodes.has(id);
  const valid = (tab: Pick<FocusTab, "review" | "diff" | "conversation" | "file">) => {
    if (tab.review && !state.changes[tab.review]?.proposal) return false;
    if (tab.diff && !state.changes[tab.diff.changeId]?.files[tab.diff.path]) return false;
    if (tab.conversation && !state.agents[tab.conversation]) return false;
    if (tab.file) {
      if (tab.file.agentId && !state.agents[tab.file.agentId]) return false;
      const path = tab.file.path;
      if (
        !state.graph?.nodes.some((node) => node.path === path) &&
        !Object.values(state.changes).some(
          (change) =>
            path in change.files && (!tab.file?.agentId || change.agent_id === tab.file.agentId),
        )
      )
        return false;
    }
    return true;
  };
  let next = view;
  for (const tab of view.tabs ?? [])
    if (!valid(tab)) next = viewReducer(next, { type: "close", id: tab.id });
  if (!view.tabs && !valid(view)) next = { ...next, review: null, diff: null, file: null };
  return {
    ...next,
    selected: hasNode(next.selected) ? next.selected : null,
    tabs: next.tabs?.map((tab) => ({
      ...tab,
      selected: hasNode(tab.selected) ? tab.selected : null,
    })),
  };
}
