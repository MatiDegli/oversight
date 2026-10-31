import { useLayoutEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import { applyEvent, stateStore, type State } from "../state";
import { progressEvents, progressMoments, progressState, type ProgressMoment } from "../agents";
import { Frame } from "./Frame";
import {
  openChange,
  navigateView,
  closePanel,
  conversationTab,
  surfaceTab,
  tabbedView,
  viewReducer,
  type View,
} from "./view";

let root: Root | undefined, current: State, navigation: View;
let setRun: (state: State) => void;
const command = () => Promise.resolve();
function Harness() {
  const [state, setState] = useState(current);
  const [view, setView] = useState<View>(tabbedView);
  useLayoutEffect(() => {
    setRun = setState;
    navigation = view;
    stateStore.setState({ state });
  }, [view, state]);
  return (
    <Frame
      state={state}
      view={view}
      onNodeTap={() => undefined}
      canOpenChange={(id) => openChange(state, view, id) !== null}
      onOpenChange={(id) => {
        setView((v) => navigateView(v, openChange(state, v, id) ?? v, state));
      }}
      onMessageAgent={command}
      onPauseAgent={command}
      onResumeAgent={command}
      onStopAgent={command}
      onOpenConversation={(id) => {
        setView((v) => viewReducer(v, { type: "open", tab: conversationTab(v, state, id) }));
      }}
      onClosePanel={() => {
        setView(closePanel);
      }}
      onTabAction={(action) => {
        setView((v) => viewReducer(v, action));
      }}
      onOpenQueue={() => {
        setView((v) => viewReducer(v, { type: "open", tab: surfaceTab(v, "inbox") }));
      }}
      canReview={() => false}
      onReview={() => undefined}
      replayLabel="Hand-written progress fixture"
      replayOf="hand-written fixture"
    />
  );
}
export const progressTest = {
  mount(moment: ProgressMoment = "working") {
    root?.unmount();
    current = progressState(moment);
    stateStore.setState({ state: current });
    const host = document.createElement("div");
    host.style.height = "100vh";
    document.body.replaceChildren(host);
    root = createRoot(host);
    flushSync(() => root?.render(<Harness />));
  },
  advance(moment: ProgressMoment) {
    current = progressEvents()
      .filter((event) => event.seq > current.lastSeq && event.seq <= progressMoments[moment])
      .reduce(applyEvent, current);
    flushSync(() => {
      setRun(current);
    });
  },
  endRound() {
    const agent = current.agents["agent-1"];
    if (!agent || !current.clock) throw new Error("Missing agent");
    current = applyEvent(current, {
      seq: current.lastSeq + 1,
      ts: current.clock,
      type: "agent.status",
      payload: { ...agent, state: "stopped" },
    });
    flushSync(() => {
      setRun(current);
    });
  },
  view: () => navigation,
};
declare global {
  interface Window {
    progressTest: typeof progressTest;
  }
}
