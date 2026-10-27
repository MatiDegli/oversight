// Browser-only test harness; never imported by the application.
import type { ServerEvent } from "@contracts";
import { useCallback, useState } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { useStore } from "zustand";

import { sendDecision } from "../review";
import type { DecisionAction, DecisionAttempt } from "../review";
import { applyEvent, createInitialState, stateStore } from "../state";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { useEscapeCloses } from "./escape";
import { Frame } from "./Frame";
import {
  clickNode,
  closePanel,
  initialView,
  nextReview,
  openChange,
  openReview,
  panelOpen,
} from "./view";
import type { View } from "./view";

const events = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);

/** What the panel asked to send; the fake server accepts each and never answers by itself. */
const decisions: { changeId: string; action: DecisionAction; comment: string }[] = [];
const accepted = () => new Promise<undefined>(() => undefined);
const client = { approveChange: accepted, rejectChange: accepted };

/** The app's wiring of view and decisions, around the real frame, without a transport. */
function Harness({ recorded }: { recorded: boolean }) {
  const state = useStore(stateStore, (store) => store.state);
  const [view, setView] = useState<View>(initialView);
  const [attempts, setAttempts] = useState<Record<string, DecisionAttempt>>({});
  const now = () => stateStore.getState().state;
  const close = useCallback(() => {
    setView(closePanel);
  }, []);
  useEscapeCloses(panelOpen(view), close);
  return (
    <Frame
      state={state}
      view={view}
      onNodeTap={(id) => {
        setView((current) => clickNode(now(), current, id));
      }}
      canOpenChange={(id) => openChange(state, view, id) !== null}
      onOpenChange={(id) => {
        setView((current) => openChange(now(), current, id) ?? current);
      }}
      onClosePanel={close}
      canReview={(id) => openReview(state, view, id) !== null}
      onReview={(id) => {
        setView((current) => openReview(now(), current, id) ?? current);
      }}
      onOpenQueue={() => {
        setView((current) => {
          const id = nextReview(now(), current.review);
          return id === null ? current : (openReview(now(), current, id) ?? current);
        });
      }}
      attempts={attempts}
      onDecide={
        recorded
          ? undefined
          : (changeId, action, comment) => {
              decisions.push({ changeId, action, comment });
              void sendDecision(client, now(), changeId, action, comment, (attempt) => {
                setAttempts((current) => ({ ...current, [changeId]: attempt }));
              });
            }
      }
    />
  );
}

export const reviewTest = {
  changed: "shop.pricing:compute_price",
  lit: events.flatMap((event) =>
    event.type === "change.proposed"
      ? [
          ...event.payload.impact.seeds,
          ...event.payload.impact.entries.map((entry) => entry.node_id),
        ]
      : [],
  ),
  decisions,
  addSecondProposal() {
    const state = stateStore.getState().state;
    const first = state.changes["change-1"];
    if (!first?.proposal) throw new Error("Missing first proposal");
    const target = "shop.orders:order_total";
    stateStore.setState({
      state: {
        ...state,
        changes: {
          ...state.changes,
          "change-2": {
            ...first,
            change_id: "change-2",
            graph: { ...first.graph, nodes_modified: [target] },
            files: Object.fromEntries(
              Object.entries(first.files).map(([path, file]) => [
                path,
                { ...file, node_ids: [target] },
              ]),
            ),
            proposal: {
              ...first.proposal,
              change_id: "change-2",
              summary: "Second proposal",
              impact: { ...first.proposal.impact, seeds: [target], entries: [] },
            },
          },
        },
      },
    });
    return target;
  },
  /** Mounts the frame on the recorded run up to `seq`; seq 20 is ready for review. */
  mount(recorded: boolean, seq = 20) {
    stateStore.setState({
      state: events.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState()),
    });
    const host = document.createElement("div");
    host.style.height = "100vh";
    document.body.replaceChildren(host);
    flushSync(() => {
      createRoot(host).render(<Harness recorded={recorded} />);
    });
  },
  /** Applies the recorded events after the current one, up to `seq`, as the stream would. */
  apply(seq: number) {
    const { state } = stateStore.getState();
    const next = events
      .filter((event) => event.seq > state.lastSeq && event.seq <= seq)
      .reduce(applyEvent, state);
    stateStore.setState({ state: next });
  },
};

declare global {
  interface Window {
    reviewTest: typeof reviewTest;
  }
}
