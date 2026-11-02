import type { ChangeResolved, MergeMode, ServerEvent } from "@contracts";
import { useReducer } from "react";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import { useStore } from "zustand";
import log from "../../../fixtures/events/handwritten-inbox-grants.jsonl?raw";
import { applyEvent, createInitialState, stateStore } from "../state";
import { parseEvent } from "../transport";
import { App } from "./App";
import { Frame } from "./Frame";
import { InitialShellLayout } from "./shell/useShell";
import { conversationTab, surfaceTab, tabbedView, viewReducer } from "./view";

// Hand-written fixture evidence, including recorded grant wording; never a model run.
const events = log.trim().split("\n").map(parseEvent);
export function chatMergeState(merged = true) {
  const state = structuredClone(events)
    .filter((e) => e.seq <= (merged ? 20 : 2))
    .reduce(applyEvent, createInitialState());
  if (state.run) state.run = { ...state.run, merge_mode: { mode: "ask_me", revision: 0 } };
  return merged ? applyEvent(state, modeEvent(21, "merge_when_safe")) : state;
}
export function modeEvent(seq: number, mode: MergeMode): ServerEvent {
  return {
    seq,
    ts: "2026-11-01T19:02:00Z",
    type: "merge_mode.changed",
    payload: { mode, revision: seq },
  };
}
let root: Root | undefined;
let calls = 0;
function Harness({ merged, commands }: { merged: boolean; commands: boolean }) {
  const state = useStore(stateStore, (s) => s.state);
  const [view, dispatch] = useReducer(
    viewReducer,
    merged
      ? viewReducer(tabbedView, {
          type: "open",
          tab: conversationTab(tabbedView, state, "agent-1"),
        })
      : tabbedView,
  );
  const command = () => {
    calls++;
    return Promise.resolve();
  };
  return (
    <Frame
      state={state}
      view={view}
      onNodeTap={() => {}}
      canOpenChange={() => false}
      onOpenChange={() => {}}
      onClosePanel={() => {
        dispatch({ type: "close", id: view.activeTab ?? "" });
      }}
      onTabAction={dispatch}
      onOpenSettings={() => {
        dispatch({ type: "open", tab: surfaceTab(view, "settings") });
      }}
      onStartAgent={commands ? command : undefined}
      onMessageAgent={commands ? command : undefined}
      onPauseAgent={commands ? command : undefined}
      onStopAgent={commands ? command : undefined}
      replayOf="hand-written demo fixture"
      replayLabel="Hand-written C5 fixture"
    />
  );
}
export const chatMergeTest = {
  mount(merged = true, app = false, commands = true) {
    root?.unmount();
    calls = 0;
    stateStore.setState({ state: chatMergeState(merged) });
    const host = document.getElementById("root");
    if (!host) throw new Error("Missing root");
    root = createRoot(host);
    flushSync(() =>
      root?.render(
        <InitialShellLayout value="Chat">
          {app ? <App /> : <Harness merged={merged} commands={commands} />}
        </InitialShellLayout>,
      ),
    );
  },
  seed(merged = true) {
    flushSync(() => {
      stateStore.setState({ state: chatMergeState(merged) });
    });
  },
  mode(mode: MergeMode) {
    flushSync(() => {
      stateStore.getState().applyEvents([modeEvent(stateStore.getState().state.lastSeq + 1, mode)]);
    });
  },
  resolve(patch: Partial<ChangeResolved>) {
    const state = stateStore.getState().state;
    const resolution = state.changes["change-1"]?.resolution;
    if (!resolution) throw new Error("Missing resolution");
    flushSync(() => {
      stateStore.getState().applyEvents([
        {
          seq: state.lastSeq + 1,
          ts: "2026-11-01T19:04:00Z",
          type: "change.resolved",
          payload: { ...resolution, ...patch },
        },
      ]);
    });
  },
  repropose() {
    const state = stateStore.getState().state;
    const proposal = state.changes["change-1"]?.proposal;
    const agent = state.agents["agent-1"];
    if (!proposal || !agent) throw new Error("Missing proposal or agent");
    flushSync(() => {
      stateStore.getState().applyEvents([
        {
          seq: state.lastSeq + 1,
          ts: "2026-11-01T19:05:00Z",
          type: "change.proposed",
          payload: { ...proposal, revision: proposal.revision + 1 },
        },
        {
          seq: state.lastSeq + 2,
          ts: "2026-11-01T19:05:01Z",
          type: "agent.status",
          payload: { ...agent, state: "ready_for_review" },
        },
      ]);
    });
  },
  messages(count: number) {
    const seq = stateStore.getState().state.lastSeq;
    flushSync(() => {
      stateStore.getState().applyEvents(
        Array.from({ length: count }, (_, i): ServerEvent => ({
          seq: seq + i + 1,
          ts: "2026-11-01T19:03:00Z",
          type: "agent.message",
          payload: { agent_id: "agent-1", text: "Hand-written C5 scroll evidence." },
        })),
      );
    });
  },
  removeProposal() {
    const state = structuredClone(stateStore.getState().state);
    const change = state.changes["change-1"];
    if (change) change.proposal = null;
    flushSync(() => {
      stateStore.setState({ state });
    });
  },
  calls: () => calls,
};
if (typeof window !== "undefined") Object.assign(window, { chatMergeTest });
declare global {
  interface Window {
    chatMergeTest: typeof chatMergeTest;
  }
}
