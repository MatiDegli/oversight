import type { ServerEvent } from "@contracts";
import { useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import { useStore } from "zustand";
import { stateStore } from "../state";
import { Frame } from "./Frame";
import { InitialShellLayout } from "./shell/useShell";
import { conversationTab, tabbedView, viewReducer } from "./view";
import { fold, scenario, status, required, event } from "./chatAgents.fixture";

let root: Root | undefined;
const calls: { id: string; text: string }[] = [];
function Harness({ name, commands }: { name: string; commands: boolean }) {
  const state = useStore(stateStore, (s) => s.state);
  const [view, setView] = useState(() =>
    name === "empty"
      ? tabbedView
      : viewReducer(tabbedView, {
          type: "open",
          tab: conversationTab(tabbedView, state, name === "merged" ? "agent-b" : "agent-a"),
        }),
  );
  return (
    <Frame
      state={state}
      view={view}
      onNodeTap={(id) => {
        setView((v) => ({ ...v, selected: id }));
      }}
      canOpenChange={() => false}
      onOpenChange={() => undefined}
      onClosePanel={() => undefined}
      onOpenConversation={(id) => {
        setView((v) => viewReducer(v, { type: "open", tab: conversationTab(v, state, id) }));
      }}
      onTabAction={(action) => {
        setView((v) => viewReducer(v, action));
      }}
      {...(commands
        ? {
            onMessageAgent: (id: string, text: string) => {
              calls.push({ id, text });
              return Promise.resolve();
            },
            onPauseAgent: () => Promise.resolve(),
            onStopAgent: () => Promise.resolve(),
          }
        : {})}
      replayLabel="Handwritten C2 agents fixture"
      replayOf="handwritten C2 agents fixture"
    />
  );
}
export const chatAgentsTest = {
  mount(name = "working", commands = true) {
    root?.unmount();
    calls.length = 0;
    stateStore.setState({ state: fold(scenario(name)) });
    root = createRoot(required(document.getElementById("root")));
    flushSync(() =>
      root?.render(
        <InitialShellLayout value="Chat">
          <Harness name={name} commands={commands} />
        </InitialShellLayout>,
      ),
    );
  },
  deliver(events: ServerEvent[]) {
    let seq = stateStore.getState().state.lastSeq;
    let runId = stateStore.getState().state.run?.run_id;
    flushSync(() => {
      stateStore.getState().applyEvents(
        events.map((e) => {
          if (e.type === "run.started" && e.payload.run_id !== runId) {
            seq = 0;
            runId = e.payload.run_id;
          }
          return { ...e, seq: ++seq };
        }),
      );
    });
  },
  longTeam() {
    this.deliver([
      event("plan.updated", {
        plan_id: "long-team",
        revision: 1,
        task: "Twelve sequential subtasks",
        graph_version: "fixture",
        state: "running",
        started_by: null,
        detail: null,
        queued_task_id: null,
        subtasks: Array.from({ length: 12 }, (_, i) => ({
          task: "Subtask",
          agent_id: `member-${String(i)}`,
          zone: { node_ids: [], paths: [] },
          depends_on: [],
          state: "running",
        })),
      }),
      ...Array.from({ length: 12 }, (_, i) =>
        status(`member-${String(i)}`, {
          plan_id: "long-team",
          state: i === 11 ? "ready_for_review" : "working",
        }),
      ),
    ]);
  },
  calls: () => calls,
};
Object.assign(window, { chatAgentsTest });
declare global {
  interface Window {
    chatAgentsTest: typeof chatAgentsTest;
  }
}
