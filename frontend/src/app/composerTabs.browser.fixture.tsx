import { useState } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { useStore } from "zustand";
import log from "../../../fixtures/events/recorded-demo.jsonl?raw";
// eslint-disable-next-line no-restricted-imports -- Reset memory-only drafts in this test harness.
import { draftStore } from "../agents/conversation/drafts";
import { applyEvent, createInitialState, stateStore } from "../state";
import { parseEvent } from "../transport";
import { Frame } from "./Frame";
import { InitialShellLayout } from "./shell/useShell";
import { closePanel, conversationTab, surfaceTab, tabbedView, viewReducer } from "./view";

let root: Root | undefined;
const calls: { command: string; id: string; text?: string }[] = [];
const control = (command: string) => (id: string, text?: string) => {
  calls.push({ command, id, ...(text === undefined ? {} : { text }) });
  return Promise.resolve();
};

function Harness() {
  const state = useStore(stateStore, (store) => store.state);
  const [view, setView] = useState(() => {
    const inbox = viewReducer(tabbedView, {
      type: "open",
      tab: surfaceTab(tabbedView, "inbox"),
    });
    return viewReducer(inbox, {
      type: "open",
      tab: conversationTab(inbox, state, "agent-1"),
    });
  });
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
      onMessageAgent={control("message")}
      onPauseAgent={control("pause")}
      onResumeAgent={control("resume")}
      onStopAgent={control("stop")}
    />
  );
}

const composerTabsTest = {
  mount() {
    root?.unmount();
    calls.length = 0;
    draftStore.setState({}, true);
    stateStore.setState({
      state: log
        .trim()
        .split("\n")
        .map(parseEvent)
        .filter((event) => event.seq <= 35)
        .reduce(applyEvent, createInitialState()),
    });
    const host = document.getElementById("root");
    if (!host) throw new Error("Missing fixture root");
    root = createRoot(host);
    flushSync(() =>
      root?.render(
        <InitialShellLayout value="Oversight">
          <Harness />
        </InitialShellLayout>,
      ),
    );
  },
  calls: () => calls,
};
Object.assign(window, { composerTabsTest });
declare global {
  interface Window {
    composerTabsTest: typeof composerTabsTest;
  }
}
