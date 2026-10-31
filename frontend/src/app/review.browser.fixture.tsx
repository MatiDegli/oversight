// Browser-only test harness; never imported by the application.
import type { ServerEvent } from "@contracts";
import { useCallback, useState } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { useStore } from "zustand";

import { createCommandClient } from "../transport";
import { createFileSession } from "./files";
import { DecisionContext } from "../review";
import { useAnswers } from "../review";
import inboxLog from "../../../fixtures/events/handwritten-inbox-grants.jsonl?raw";
import { navigateView, surfaceTab, tabbedView, viewReducer } from "./view";
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
const client = { approveChange: accepted, rejectChange: accepted, requestChanges: accepted };

const commands = createCommandClient();
let fileReadGate = Promise.resolve();
let releaseFileReads = () => {};
const files = createFileSession({
  async getFile(path, agentId) {
    await fileReadGate;
    const state = stateStore.getState().state;
    const change = Object.values(state.changes).find((item) => item.agent_id === agentId);
    const content = change?.files[path]?.file.modified ?? "";
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(content));
    const hash = [...new Uint8Array(digest)]
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
    return { path, agent_id: agentId ?? null, content, hash };
  },
  saveFile: commands.saveFile,
});

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
      files={recorded ? undefined : files}
      onStopAgent={recorded ? undefined : commands.stopAgent}
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
      replayOf={recorded ? "hand-written demo fixture" : undefined}
      onDecide={
        recorded
          ? undefined
          : (changeId, action, comment) => {
              decisions.push({ changeId, action, comment });
              void sendDecision(client, state, changeId, action, comment, (attempt) => {
                setAttempts((current) => ({ ...current, [changeId]: attempt }));
              });
            }
      }
    />
  );
}

export const reviewTest = {
  holdFileReads() {
    fileReadGate = new Promise<void>((resolve) => {
      releaseFileReads = resolve;
    });
  },
  releaseFileReads() {
    releaseFileReads();
  },
  emit(event: ServerEvent) {
    const state = stateStore.getState().state;
    stateStore.setState({ state: applyEvent(state, { ...event, seq: state.lastSeq + 1 }) });
  },
  state() {
    return stateStore.getState().state;
  },
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

// Item 9 command harness uses the same shared attempts and command client as App.
let commandsRoot: ReturnType<typeof createRoot> | undefined;
function InboxCommands({ recorded }: { recorded: boolean }) {
  const state = useStore(stateStore, (s) => s.state);
  const [view, setView] = useState<View>(tabbedView);
  const [attempts, setAttempts] = useState<Record<string, DecisionAttempt>>({});
  const onDecide = (id: string, action: DecisionAction, comment: string) => {
    void sendDecision(commands, state, id, action, comment, (attempt) => {
      setAttempts((old) => ({ ...old, [id]: attempt }));
    });
  };
  const review = (id: string) => {
    setView((v) => navigateView(v, { ...v, review: id, diff: null, file: null }, state));
  };
  return (
    <DecisionContext
      value={{
        attempts,
        onDecide: recorded ? undefined : onDecide,
        revokeGrant: recorded ? undefined : commands.revokeGrant,
      }}
    >
      <Frame
        state={state}
        view={view}
        attempts={attempts}
        onDecide={recorded ? undefined : onDecide}
        onNodeTap={() => {}}
        canOpenChange={() => true}
        onOpenChange={review}
        onReview={review}
        canReview={() => true}
        onClosePanel={() => {
          setView(closePanel);
        }}
        onOpenQueue={() => {
          setView((v) => viewReducer(v, { type: "open", tab: surfaceTab(v, "inbox") }));
        }}
        onTabAction={(action) => {
          setView((v) => viewReducer(v, action));
        }}
      />
    </DecisionContext>
  );
}
export function mountInboxCommands(recorded = false, twoFiles = false) {
  commandsRoot?.unmount();
  useAnswers.setState({ drafts: {}, grants: {} });
  const state = inboxLog
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as ServerEvent)
    .reduce(applyEvent, createInitialState());
  const p = state.changes["change-2"]?.proposal;
  if (twoFiles && p?.files[0]) p.files.push({ ...p.files[0], path: "shop/orders.py" });
  stateStore.setState({ state });
  const host = document.createElement("div");
  host.style.height = "100vh";
  document.body.replaceChildren(host);
  commandsRoot = createRoot(host);
  flushSync(() => commandsRoot?.render(<InboxCommands recorded={recorded} />));
}
