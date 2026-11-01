import { useRef, useState } from "react";
import type { ChangeProposed } from "@contracts";
import type { State } from "../state";
import { changeNodeIds, tabbedView, type View, type ViewAction } from "./view";
import type { ShellLayout } from "./shell/useShell";
import { sameCandidate } from "./chatReviewSelectors";

export interface GraphIntent {
  runId: string;
  proposal: ChangeProposed;
  key: string;
}

/** Click-owned target; resolved reviews may frame too, without using proposed-only openReview. */
export function graphReviewAction(state: State, intent: GraphIntent): ViewAction | null {
  const change = state.changes[intent.proposal.change_id];
  if (
    state.run?.run_id !== intent.runId ||
    !change?.proposal ||
    !sameCandidate(change.proposal, intent.proposal)
  )
    return null;
  return {
    type: "open",
    tab: {
      id: `review:${change.change_id}`,
      review: change.change_id,
      diff: null,
      file: null,
      selected: changeNodeIds(change)[0] ?? null,
      frameIds: changeNodeIds(change),
      frameKey: intent.key,
    },
  };
}

/** Hidden navigation is frozen. No arrival or lazy completion dispatches or selects a layout. */
export function useChatReviewNavigation(
  state: State,
  view: View,
  shell: {
    layout: ShellLayout;
    setLayout: (layout: ShellLayout) => void;
  },
  dispatch?: (action: ViewAction) => void,
) {
  const serial = useRef(0);
  const [intent, setIntent] = useState<GraphIntent | null>(null);
  const [cancelledView, setCancelledView] = useState<View | null>(null);
  const [snapshot, setSnapshot] = useState({
    runId: state.run?.run_id,
    view,
    layout: shell.layout,
  });
  const replaced = snapshot.runId !== state.run?.run_id;
  const abandoned = snapshot.layout === "Oversight" && shell.layout === "Chat";
  const invalid = intent !== null && graphReviewAction(state, intent) === null;
  if ((replaced || invalid) && cancelledView !== view) setCancelledView(view);
  if (intent && (replaced || abandoned || invalid || view.review !== intent.proposal.change_id))
    setIntent(null);
  // A hidden/cold graph must not finish a discarded frame. The existing Usage navigation
  // preserves its camera and suppresses initial framing, without any new graph API.
  const frozen = abandoned && intent ? { ...snapshot.view, activeTab: "usage" } : snapshot.view;
  const graphView =
    replaced || invalid || cancelledView === view
      ? tabbedView
      : shell.layout === "Oversight"
        ? view
        : frozen;
  if (
    replaced ||
    snapshot.layout !== shell.layout ||
    (shell.layout === "Oversight" && snapshot.view !== graphView)
  ) {
    setSnapshot({ runId: state.run?.run_id, view: graphView, layout: shell.layout });
  }
  return {
    graphView,
    onGraph:
      dispatch && state.run
        ? (changeId: string) => {
            const proposal = state.changes[changeId]?.proposal;
            if (!proposal || !state.run) return;
            const target = {
              runId: state.run.run_id,
              proposal,
              key: `graph:${state.run.run_id}:${changeId}:${String(proposal.revision)}:${proposal.head_commit}:${String(++serial.current)}`,
            };
            const action = graphReviewAction(state, target);
            if (!action) return;
            setIntent(target);
            dispatch(action);
            shell.setLayout("Oversight");
          }
        : undefined,
  };
}
