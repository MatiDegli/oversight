import type { ServerEvent } from "@contracts";
import "../theme/index.css";
import { useState } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { useStore } from "zustand";
import { createInitialState, stateStore } from "../state";
import { DecisionContext, selectPlanEvidence } from "../review";
import { startLive, startReplay, type EventStream, type Transport } from "../transport";
import { Frame } from "./Frame";
import { InitialShellLayout, type ShellLayout } from "./shell/useShell";
import { conversationTab, openReview, surfaceTab, tabbedView, viewReducer } from "./view";
import { flowEvents, flowPlan, type FlowScene } from "./planFlow.browser.fixture";

export type WorkflowOptions = {
  layout?: ShellLayout;
  inbox?: boolean;
  scene?: FlowScene;
  mode?: "live" | "replay";
  prefix?: number;
};
let root: Root | undefined;
let transport: Transport | undefined;
let stream: EventStream | undefined;
let source: ServerEvent[] = [];
let options: WorkflowOptions = {};
export const navigation: string[] = [];

function Harness() {
  const state = useStore(stateStore, (store) => store.state);
  const [view, setView] = useState(() =>
    viewReducer(tabbedView, {
      type: "open",
      tab: options.inbox
        ? surfaceTab(tabbedView, "inbox")
        : conversationTab(tabbedView, state, "lead-flow"),
    }),
  );
  const review = (id: string) => {
    navigation.push(id);
    setView((current) => openReview(state, current, id) ?? current);
  };
  return (
    <InitialShellLayout value={options.layout ?? "Chat"}>
      <DecisionContext value={{ replayOf: "synthetic workflow events" }}>
        <Frame
          state={state}
          view={view}
          onReview={review}
          onOpenChange={review}
          canReview={() => true}
          canOpenChange={() => true}
          onNodeTap={() => undefined}
          onClosePanel={() => {
            setView(tabbedView);
          }}
          onTabAction={(action) => {
            setView((current) => viewReducer(current, action));
          }}
          onOpenQueue={() => {
            setView((current) =>
              viewReducer(current, {
                type: "open",
                tab: surfaceTab(current, "inbox"),
              }),
            );
          }}
          replayLabel="Synthetic workflow evidence · scripted events"
        />
      </DecisionContext>
    </InitialShellLayout>
  );
}

export async function mountWorkflow(next: WorkflowOptions = {}) {
  transport?.stop();
  root?.unmount();
  options = next;
  navigation.length = 0;
  source = flowEvents(next.scene ?? "gates").map((event, index) => ({
    ...event,
    seq: index + 1,
    ts: `2026-11-03T19:00:${String(index).padStart(2, "0")}Z`,
  }));
  stateStore.setState({ state: createInitialState() });
  if (next.mode === "replay") await replayThrough(next.prefix ?? source.length);
  else {
    transport = startLive({
      store: stateStore,
      openStream: () => {
        stream = { onmessage: null, onerror: null, readyState: 1, close: () => {} };
        return stream;
      },
    });
    ingestThrough(next.prefix ?? source.length);
  }
  const host = document.createElement("div");
  host.style.height = "100vh";
  document.body.replaceChildren(host);
  root = createRoot(host);
  flushSync(() => {
    root?.render(<Harness />);
  });
}

/** Real live transport parses and applies each synthetic SSE message. */
export function ingestThrough(prefix: number) {
  for (const event of source.slice(stateStore.getState().state.lastSeq, prefix))
    stream?.onmessage?.(new MessageEvent("message", { data: JSON.stringify(event) }));
}

/** Real replay transport parses the same inclusive prefix and invokes production applyEvent. */
export async function replayThrough(prefix: number) {
  transport?.stop();
  transport = startReplay({
    store: stateStore,
    source: {
      text: source
        .slice(0, prefix)
        .map((event) => JSON.stringify(event))
        .join("\n"),
    },
    speed: 0,
  });
  await transport.ready;
}

function append(event: ServerEvent) {
  const next = { ...event, seq: source.length + 1, ts: "2026-11-03T19:01:00Z" };
  source.push(next);
  ingestThrough(source.length);
}
export function arrive() {
  const plan = flowPlan(true);
  append({
    type: "plan.updated",
    seq: 0,
    ts: "",
    payload: {
      ...plan,
      plan_id: "another-plan",
      task: "Another plan must not replace your workflow",
    },
  });
  const current = selectPlanEvidence(stateStore.getState().state).plans.get(
    "plan-checkout",
  )?.payload;
  if (!current) throw new Error("Missing synthetic plan");
  append({
    type: "plan.updated",
    seq: 0,
    ts: "",
    payload: {
      ...current,
      revision: current.revision + 1,
      subtasks:
        current.subtasks?.map((task, index) =>
          index === 0 ? { ...task, task: "Updated evidence for the first task" } : task,
        ) ?? null,
    },
  });
}
export function removeSelected() {
  const plan = selectPlanEvidence(stateStore.getState().state).plans.get("plan-checkout")?.payload;
  if (!plan) throw new Error("Missing synthetic plan");
  append({
    type: "plan.updated",
    seq: 0,
    ts: "",
    payload: {
      ...plan,
      revision: plan.revision + 1,
      subtasks: plan.subtasks?.slice(0, 2) ?? null,
    },
  });
}
export const prefixLength = () => source.length;
export const currentSeq = () => stateStore.getState().state.lastSeq;
