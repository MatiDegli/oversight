import type { AgentStatus, LeadStatus, ServerEvent } from "@contracts";
import { useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import { useStore } from "zustand";
import { stateStore } from "../state";
import { createCommandClient } from "../transport";
// eslint-disable-next-line no-restricted-imports -- Fixture resets only the drafts owned by this front.
import { leadDraftStore } from "../agents/leadDraftModel";
// eslint-disable-next-line no-restricted-imports -- Same persistent TaskBox used by production.
import { taskBoxStore } from "../agents/taskBoxModel";
import { Frame } from "./Frame";
import { App } from "./App";
import { InitialShellLayout } from "./shell/useShell";
import { closePanel, conversationTab, tabbedView, viewReducer, surfaceTab } from "./view";
import { fold, plan as oldPlan, seed, status as oldStatus, required } from "./chatAgents.fixture";

const timestamp = "2026-11-02T18:00:00.000Z";
function event<T extends ServerEvent["type"]>(
  type: T,
  payload: Extract<ServerEvent, { type: T }>["payload"],
) {
  return { type, payload, seq: 0, ts: timestamp } as ServerEvent;
}
function status(id: string, overrides: Partial<AgentStatus> = {}) {
  return {
    ...oldStatus(id, { change_id: `change-${id.split("-")[1] ?? "1"}`, ...overrides }),
    ts: timestamp,
  };
}
function plan(id: string, owner: string) {
  return { ...oldPlan(id, owner), ts: timestamp };
}

export function lead(id = "agent-1", overrides: Partial<LeadStatus> = {}) {
  return event("lead.status", {
    agent_id: id,
    revision: 1,
    task: "Make the checkout safer: validate prices and quantities, and cover the edge cases.",
    model: "muse-spark",
    state: "idle",
    step: 10,
    detail: "Requested plan-3.",
    limits: null,
    requested_plan_id: null,
    ...overrides,
  });
}
export const limits = {
  turns: 50,
  plans: 3,
  tool_calls: 12,
  model_calls: 18,
  wall_clock_s: 300,
  subtasks: 6,
  task_chars: 2000,
};
const runSeed = seed.map((item) =>
  item.type === "run.started"
    ? {
        ...item,
        payload: {
          ...item.payload,
          contract_version: "1.16.0",
          merge_mode: { mode: "ask_me" as const, revision: 0 },
        },
      }
    : item,
);
export function leadScenario(name: string): ServerEvent[] {
  if (name === "same-model")
    return runSeed.map((item) =>
      item.type === "run.started"
        ? {
            ...item,
            payload: {
              ...item.payload,
              slots: item.payload.slots.map((slot) => ({ ...slot, default_model: "muse-spark" })),
            },
          }
        : item,
    );
  if (name === "chat-start" || name === "lead-new") return runSeed;
  if (name === "task-direct")
    return [...runSeed, status("agent-5", { task: "Reject negative prices and quantities." })];
  return [
    ...runSeed,
    lead(),
    lead("agent-2", {
      task: "Find the inventory edge cases and propose a focused test plan.",
      state: "working",
      step: 4,
      detail: "Reading inventory.py",
    }),
    plan("plan-1", "agent-1"),
    status("agent-3", { plan_id: "plan-1", task: "Reject negative unit prices.", slot: "A" }),
    status("agent-4", {
      plan_id: "plan-1",
      task: "Validate quantities.",
      state: "ready_for_review",
      slot: "B",
    }),
    status("agent-5", { state: "done", task: "Document checkout validation." }),
    event("lead.reply", {
      agent_id: "agent-1",
      answers_seq: null,
      text: "I split validation and its edge-case tests into a focused plan. You can queue it after review.",
      through_seq: 3,
      cut: null,
    }),
  ];
}
let root: Root | undefined;
const commands = createCommandClient();
function Harness({ name, live }: { name: string; live: boolean }) {
  const state = useStore(stateStore, (store) => store.state);
  const [view, setView] = useState(() =>
    name === "leads-two" || name === "lead-switch"
      ? viewReducer(tabbedView, {
          type: "open",
          tab: conversationTab(tabbedView, state, name === "lead-switch" ? "agent-2" : "agent-1"),
        })
      : tabbedView,
  );
  return (
    <Frame
      state={state}
      view={view}
      onNodeTap={() => undefined}
      canOpenChange={() => false}
      onOpenChange={() => undefined}
      onClosePanel={() => {
        setView(closePanel);
      }}
      onTabAction={(action) => {
        setView((current) => viewReducer(current, action));
      }}
      onOpenConversation={(id) => {
        setView((current) =>
          viewReducer(current, { type: "open", tab: conversationTab(current, state, id) }),
        );
      }}
      onOpenSettings={() => {
        setView((current) =>
          viewReducer(current, { type: "open", tab: surfaceTab(current, "settings") }),
        );
      }}
      onOpenQueue={() => {
        setView((current) =>
          viewReducer(current, { type: "open", tab: surfaceTab(current, "inbox") }),
        );
      }}
      onStartAgent={live ? commands.startAgent : undefined}
      onStartLead={live ? commands.startLead : undefined}
      onMessageLead={live ? (id, text) => commands.messageLead(id, { text }) : undefined}
      replayLabel="Handwritten K2a fixture"
    />
  );
}
export const chatLeadsTest = {
  mount(name = "chat-start", live = true, app = false) {
    root?.unmount();
    taskBoxStore.setState({ draft: "", pending: false, failure: null, choice: null });
    leadDraftStore.setState({ draft: "", pending: false, failure: null, choice: null });
    stateStore.setState({ state: fold(leadScenario(name)) });
    root = createRoot(required(document.getElementById("root")));
    flushSync(() =>
      root?.render(
        <InitialShellLayout value="Chat">
          {app ? <App /> : <Harness name={name} live={live} />}
        </InitialShellLayout>,
      ),
    );
  },
  deliver(events: ServerEvent[]) {
    let seq = stateStore.getState().state.lastSeq;
    flushSync(() => {
      stateStore.getState().applyEvents(events.map((item) => ({ ...item, seq: ++seq })));
    });
  },
  events: () => stateStore.getState().state.decisionEvents,
  prefix: (name: string) => leadScenario(name).map((item, i) => ({ ...item, seq: i + 1 })),
};
Object.assign(window, { chatLeadsTest });
declare global {
  interface Window {
    chatLeadsTest: typeof chatLeadsTest;
  }
}
