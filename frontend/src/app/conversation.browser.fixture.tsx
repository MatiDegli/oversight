import { useCallback, useLayoutEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import type { ServerEvent } from "@contracts";
import log from "../../../fixtures/events/recorded-demo.jsonl?raw";
import { applyEvent, createInitialState, stateStore, type State } from "../state";
import { parseEvent } from "../transport";
import { Frame, type FrameProps } from "./Frame";
import {
  closePanel,
  conversationTab,
  navigateView,
  openChange,
  surfaceTab,
  tabbedView,
  viewReducer,
} from "./view";
import type { View } from "./view";

const events = log.trim().split("\n").map(parseEvent);
let root: Root | undefined;
let setRun: (state: State) => void;
let current: State;
let openConversation: (id: string) => void;
let navigation: View;
const calls: { command: string; id: string; text?: string }[] = [];
const control = (command: string) => (id: string, text?: string) => {
  calls.push({ command, id, ...(text === undefined ? {} : { text }) });
  return Promise.resolve();
};
function Harness({ commands }: { commands: boolean }) {
  const [state, setState] = useState(current);
  const [view, setView] = useState<View>(tabbedView);
  const open = useCallback(
    (id: string) => {
      setView((v) => viewReducer(v, { type: "open", tab: conversationTab(v, state, id) }));
    },
    [state],
  );
  useLayoutEffect(() => {
    setRun = setState;
    openConversation = open;
    navigation = view;
    stateStore.setState({ state });
  }, [open, view, state]);
  const controls: Partial<FrameProps> = commands
    ? {
        onMessageAgent: control("message"),
        onPauseAgent: control("pause"),
        onResumeAgent: control("resume"),
        onStopAgent: control("stop"),
      }
    : {};
  return (
    <Frame
      {...controls}
      state={state}
      view={view}
      onNodeTap={() => undefined}
      canOpenChange={(id) => openChange(state, view, id) !== null}
      onOpenChange={(id) => {
        setView((v) => navigateView(v, openChange(state, v, id) ?? v, state));
      }}
      onOpenConversation={open}
      onClosePanel={() => {
        setView(closePanel);
      }}
      onTabAction={(action) => {
        setView((v) => viewReducer(v, action));
      }}
      onOpenQueue={() => {
        setView((v) => viewReducer(v, { type: "open", tab: surfaceTab(v, "inbox") }));
      }}
      canReview={(id) => state.changes[id]?.status === "ready_for_review"}
      onReview={(id) => {
        setView((v) => navigateView(v, { ...v, review: id, diff: null, file: null }, state));
      }}
      replayLabel="Recorded run · Oct 27, 2026"
      replayOf="recorded run"
    />
  );
}
export const conversationTabTest = {
  mount(commands = true, end = 35) {
    root?.unmount();
    calls.length = 0;
    current = events.filter((event) => event.seq <= end).reduce(applyEvent, createInitialState());
    stateStore.setState({ state: current });
    const host = document.createElement("div");
    host.style.height = "100vh";
    document.body.replaceChildren(host);
    root = createRoot(host);
    flushSync(() => root?.render(<Harness commands={commands} />));
  },
  open(id: string) {
    flushSync(() => {
      openConversation(id);
    });
  },
  apply(event: ServerEvent) {
    current = applyEvent(current, event);
    flushSync(() => {
      setRun(current);
    });
  },
  advance(end: number) {
    current = events
      .filter((event) => event.seq > current.lastSeq && event.seq <= end)
      .reduce(applyEvent, current);
    flushSync(() => {
      setRun(current);
    });
  },
  state: () => current,
  view: () => navigation,
  calls: () => calls,
};
declare global {
  interface Window {
    conversationTabTest: typeof conversationTabTest;
  }
}
