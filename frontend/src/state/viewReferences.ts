import type { LeadView, LeadViewKind, ServerEvent } from "@contracts";

import { resolvePreviewReference, type PreviewResolution } from "./previewReferences";
import { selectLiveDiff } from "./selectors";
import type { State } from "./types";

export const MOMENT_EVENT_TYPES: ReadonlySet<ServerEvent["type"]> = new Set([
  "change.proposed",
  "change.resolved",
  "change.reviewed",
  "tests.executed",
  "plan.updated",
  "revert.resolved",
  "lead.reply",
  "error",
]);
const previewTargets = new WeakMap<PreviewResolution, string[]>();

/** Resolve identities again at the displayed state; retain the original request separately. */
export function resolveViewReferences(
  state: State,
  request: LeadView,
): (string | number | [string, string])[] {
  const view = request.view;
  switch (view.kind) {
    case "highlight":
      return resolveHighlights(state, view);
    case "files":
      return view.references
        .filter((ref) => selectLiveDiff(state, ref.change_id, ref.path) !== undefined)
        .map((ref): [string, string] => [ref.change_id, ref.path]);
    case "behavior":
      return hasMeasuredBehavior(state, view.change_id) ? [view.change_id] : [];
    case "preview": {
      const resolved = resolvePreviewReference(state, request);
      let targets = previewTargets.get(resolved);
      if (!targets) {
        targets = resolved.candidate ? [resolved.candidate.change_id] : [];
        previewTargets.set(resolved, targets);
      }
      return targets;
    }
    case "moment":
      return view.event_seq <= request.through_seq &&
        view.event_seq <= state.lastSeq &&
        state.eventFacts[view.event_seq] !== undefined &&
        MOMENT_EVENT_TYPES.has(state.eventFacts[view.event_seq]?.type ?? "run.started")
        ? [view.event_seq]
        : [];
    default: {
      const unknown: Exclude<
        LeadViewKind,
        { kind: "highlight" | "files" | "behavior" | "moment" }
      > = view;
      // eslint-disable-next-line @typescript-eslint/no-meaningless-void-operator -- Today never; future contract kinds intentionally render nothing.
      void unknown;
      return [];
    }
  }
}

function resolveHighlights(
  state: State,
  view: Extract<LeadViewKind, { kind: "highlight" }>,
): string[] {
  const nodes = new Map(state.graph?.nodes.map((node) => [node.id, node]));
  for (const change of Object.values(state.changes)) {
    if (change.status !== "approved" && change.status !== "rejected") {
      for (const node of change.graph.nodes_added)
        if (!nodes.has(node.id)) nodes.set(node.id, node);
    }
  }
  const result: string[] = [];
  for (const ref of view.references) {
    if (ref.kind === "path") {
      result.push(...[...nodes.values()].filter((n) => n.path === ref.path).map((n) => n.id));
    } else {
      const agent = state.agents[ref.agent_id];
      if (!agent) continue;
      const graph = state.changes[agent.change_id]?.graph;
      result.push(
        ...[
          ...(graph?.nodes_added.map((n) => n.id) ?? []),
          ...(graph?.nodes_modified ?? []),
          ...(graph?.nodes_removed ?? []),
          ...(state.focus[ref.agent_id]?.node_ids ?? []),
        ].filter((id) => nodes.has(id)),
      );
    }
  }
  return [...new Set(result)];
}

/** Latest whole facts for the current revision/head, matching the review evidence selector. */
function hasMeasuredBehavior(state: State, changeId: string): boolean {
  const proposal = state.changes[changeId]?.proposal;
  if (!proposal) return false;
  const facts = state.changeFacts[changeId]?.findLast(
    (candidate) =>
      candidate.revision === proposal.revision &&
      candidate.head_commit === proposal.head_commit &&
      candidate.change_id === changeId,
  );
  return facts?.behavior_diff?.some((node) => node.compared_count > 0) ?? false;
}
