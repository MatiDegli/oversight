import { useState } from "react";
import { createRoot } from "react-dom/client";
import { useStore } from "zustand";
import { applyEvent, createInitialState, stateStore } from "../state";
import { parseEvent, replayRecording, replaySource } from "../transport";
import { Frame } from "./Frame";
import { FilesProvider } from "./FilesContext";
import { createRecordedFiles } from "./recordedFiles";
import { usePractice } from "./challenge/usePractice";
import { availablePractice } from "./challenge/availability";
import type { Attempt } from "./challenge/attempt";
import { PracticeFrame } from "./shell/PanelFallbacks";
import {
  closePanel,
  conversationTab,
  navigateView,
  openReview,
  surfaceTab,
  tabbedView,
  viewReducer,
} from "./view";

const state = replayRecording.text
  .trim()
  .split("\n")
  .map(parseEvent)
  .filter((e) => e.seq <= 63)
  .reduce(applyEvent, createInitialState());
stateStore.setState({ state });
const files = createRecordedFiles(() => stateStore.getState().state);
function Harness() {
  const state = useStore(stateStore, (s) => s.state);
  const [view, setView] = useState(tabbedView);
  const practice = usePractice(
    setView,
    () => {},
    () => {},
    true,
    replaySource,
  );
  const review = (id: string) => {
    setView((v) => navigateView(v, openReview(state, v, id) ?? v, state));
  };
  return (
    <FilesProvider
      state={state}
      openFile={(path) => {
        setView((v) =>
          navigateView(
            v,
            {
              ...v,
              review: null,
              diff: null,
              file: { path, agentId: null, startLine: 1, endLine: 1 },
            },
            state,
          ),
        );
      }}
    >
      <Frame
        state={state}
        view={view}
        files={files}
        practice={practice}
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
        onOpenConversation={(id) => {
          setView((v) => viewReducer(v, { type: "open", tab: conversationTab(v, state, id) }));
        }}
        onTabAction={(action) => {
          setView((v) => viewReducer(v, action));
        }}
        replayOf="real recorded run"
        replayLabel={replaySource.label}
      />
    </FilesProvider>
  );
}
const host = document.getElementById("root");
if (!host) throw new Error("Missing root");
const root = createRoot(host);
root.render(<Harness />);

declare global {
  interface Window {
    startupPracticeFrame: (attempt: Attempt, loaded: boolean) => Promise<void>;
  }
}
window.startupPracticeFrame = async (attempt, loaded) => {
  const { PracticePanel } = await import("./challenge/PracticePanel");
  const practice = availablePractice(true, replaySource, replayRecording);
  if (!practice) throw new Error("Missing recorded practice");
  const onExit = () => {};
  root.render(
    <div className="focus-body" style={{ height: "100vh" }}>
      {loaded ? (
        <PracticePanel
          practice={practice}
          attempt={attempt}
          onExit={onExit}
          onStart={() => {}}
          onAnswer={() => {}}
          onNext={() => {}}
          onReveal={() => {}}
          onRestart={() => {}}
        />
      ) : (
        <PracticeFrame attempt={attempt} onExit={onExit} />
      )}
    </div>,
  );
};
