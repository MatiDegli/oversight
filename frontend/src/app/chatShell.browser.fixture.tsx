import type { ServerEvent } from "@contracts";
import { useLayoutEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import { useStore } from "zustand";
import log from "../../../fixtures/events/recorded-demo.jsonl?raw";
import { applyEvent, createInitialState, stateStore } from "../state";
import { parseEvent } from "../transport";
import { App } from "./App";
import { Frame } from "./Frame";
import { InitialShellLayout, type ShellLayout } from "./shell/useShell";
import { closePanel, conversationTab, tabbedView, viewReducer, type View } from "./view";

let webglRequests = 0;
// eslint-disable-next-line @typescript-eslint/unbound-method -- The proxy forwards the original canvas receiver.
HTMLCanvasElement.prototype.getContext = new Proxy(HTMLCanvasElement.prototype.getContext, {
  apply(target, canvas, args: unknown[]) {
    if (args[0] === "webgl2" || args[0] === "webgl") webglRequests++;
    return Reflect.apply(target, canvas, args) as RenderingContext | null;
  },
});
const events = log.trim().split("\n").map(parseEvent);
let root: Root | undefined;
let navigation: View;
const calls: { command: string; id: string; text?: string }[] = [];
const control = (command: string) => (id: string, text?: string) => {
  calls.push({ command, id, text });
  return Promise.resolve();
};
function Harness({ commands, working }: { commands: boolean; working: boolean }) {
  const state = useStore(stateStore, (store) => store.state);
  const [view, setView] = useState(() =>
    working
      ? viewReducer(tabbedView, {
          type: "open",
          tab: conversationTab(tabbedView, state, "agent-1"),
        })
      : tabbedView,
  );
  useLayoutEffect(() => {
    navigation = view;
  }, [view]);
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
      onOpenConversation={(id) => {
        setView((v) => viewReducer(v, { type: "open", tab: conversationTab(v, state, id) }));
      }}
      onTabAction={(action) => {
        setView((v) => viewReducer(v, action));
      }}
      {...(commands
        ? {
            onStartAgent: () => Promise.resolve(),
            onMessageAgent: control("message"),
            onPauseAgent: control("pause"),
            onResumeAgent: control("resume"),
            onStopAgent: control("stop"),
          }
        : {})}
      replayLabel={
        commands ? "Fixture controls · recorded event data" : "Recorded run · Oct 27, 2026"
      }
      replayOf="recorded run"
    />
  );
}
export const chatShellTest = {
  mount({
    initial = "Oversight",
    working = true,
    commands = true,
    app = false,
  }: {
    initial?: ShellLayout;
    working?: boolean;
    commands?: boolean;
    app?: boolean;
  } = {}) {
    root?.unmount();
    calls.length = 0;
    stateStore.setState({
      state: events
        .filter((e) => e.seq <= (working ? 35 : 2))
        .reduce(applyEvent, createInitialState()),
    });
    const host = document.getElementById("root");
    if (!host) throw new Error("Missing fixture root");
    root = createRoot(host);
    flushSync(() =>
      root?.render(
        <InitialShellLayout value={initial}>
          {app ? <App /> : <Harness commands={commands} working={working} />}
        </InitialShellLayout>,
      ),
    );
  },
  advance(end: number) {
    const state = stateStore.getState().state;
    flushSync(() => {
      stateStore
        .getState()
        .applyEvents(events.filter((e) => e.seq > state.lastSeq && e.seq <= end));
    });
  },
  messages(count: number) {
    const state = stateStore.getState().state;
    flushSync(() => {
      stateStore.getState().applyEvents(
        Array.from({ length: count }, (_, i): ServerEvent => ({
          seq: state.lastSeq + i + 1,
          ts: state.clock ?? "2026-10-27T18:53:00Z",
          type: "agent.message",
          payload: { agent_id: "agent-1", text: "Fixture message for independent scrolling." },
        })),
      );
    });
  },
  view: () => navigation,
  calls: () => calls,
  seq: () => stateStore.getState().state.lastSeq,
  contexts: () => webglRequests,
};
Object.assign(window, { chatShellTest });
declare global {
  interface Window {
    chatShellTest: typeof chatShellTest;
  }
}
