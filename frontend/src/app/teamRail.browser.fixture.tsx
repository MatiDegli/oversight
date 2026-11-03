import type { GraphSnapshot, ServerEvent } from "@contracts";
import { useState } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { useStore } from "zustand";
import { stateStore } from "../state";
import { parseEvent } from "../transport";
import { Frame } from "./Frame";
import { FilesProvider } from "./FilesContext";
import { InitialShellLayout } from "./shell/useShell";
import { conversationTab, tabbedView, viewReducer } from "./view";
import { railState, type RailScene } from "./teamRail.fixture";

let root: Root | undefined;
const opened: string[] = [];
const calls: { id: string; text: string }[] = [];
let resolveMessage: (() => void) | undefined;
function Harness() {
  const state = useStore(stateStore, (s) => s.state);
  const [view, setView] = useState(() =>
    viewReducer(tabbedView, { type: "open", tab: conversationTab(tabbedView, state, "a1") }),
  );
  return (
    <FilesProvider
      state={state}
      openFile={(path) => {
        opened.push(path);
      }}
    >
      <Frame
        state={state}
        view={view}
        onNodeTap={() => undefined}
        canOpenChange={() => false}
        onOpenChange={() => undefined}
        onClosePanel={() => undefined}
        onTabAction={(action) => {
          setView((current) => viewReducer(current, action));
        }}
        onOpenConversation={(id) => {
          setView((current) =>
            viewReducer(current, { type: "open", tab: conversationTab(current, state, id) }),
          );
        }}
        onMessageAgent={(id, text) => {
          calls.push({ id, text });
          return new Promise<void>((resolve) => {
            resolveMessage = resolve;
          });
        }}
        onMessageLead={() => Promise.resolve()}
        onStopLead={() => Promise.resolve()}
        onPauseAgent={() => Promise.resolve()}
        onStopAgent={() => Promise.resolve()}
        replayLabel="handwritten · team rail"
        replayOf="handwritten team rail events"
      />
    </FilesProvider>
  );
}
export const teamRailTest = {
  mount(scene: RailScene = "chat-busy", graph?: GraphSnapshot) {
    root?.unmount();
    opened.length = 0;
    calls.length = 0;
    stateStore.setState({ state: railState(scene, graph) });
    const host = document.getElementById("root");
    if (!host) throw new Error("Missing team rail host");
    root = createRoot(host);
    flushSync(() =>
      root?.render(
        <InitialShellLayout value={scene === "agents-folded-overflow" ? "Oversight" : "Chat"}>
          <Harness />
        </InitialShellLayout>,
      ),
    );
  },
  deliver(events: ServerEvent[]) {
    let seq = stateStore.getState().state.lastSeq;
    flushSync(() => {
      stateStore
        .getState()
        .applyEvents(events.map((e) => parseEvent(JSON.stringify({ ...e, seq: ++seq }))));
    });
  },
  finishMessage() {
    resolveMessage?.();
  },
  opened: () => opened,
  calls: () => calls,
  prefix: () => stateStore.getState().state.lastSeq,
};
Object.assign(window, { teamRailTest });
declare global {
  interface Window {
    teamRailTest: typeof teamRailTest;
  }
}
