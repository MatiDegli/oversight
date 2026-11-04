import { useState } from "react";
import { createRoot } from "react-dom/client";
import fixture from "../../../../fixtures/events/handwritten-level1.jsonl?raw";
import { parseEvent } from "../../transport";
import { applyEvent, createInitialState } from "../../state";
import { Frame } from "../Frame";
import { surfaceTab, tabbedView, viewReducer } from "../view";

const host = document.getElementById("root");
if (!host) throw new Error("Missing root");
const root = createRoot(host);
function Harness({ live }: { live: boolean }) {
  const [view, setView] = useState(tabbedView);
  const state = {
    ...fixture
      .trim()
      .split("\n")
      .map(parseEvent)
      .filter((event) => event.seq <= 20)
      .reduce(applyEvent, createInitialState()),
    mergeMode: { mode: "ask_me", revision: 0 } as const,
  };
  return (
    <Frame
      state={state}
      view={view}
      onNodeTap={() => {}}
      canOpenChange={() => false}
      onOpenChange={() => {}}
      onOpenQueue={() => {}}
      onClosePanel={() => {
        setView(tabbedView);
      }}
      onTabAction={(action) => {
        setView((old) => viewReducer(old, action));
      }}
      onOpenSettings={() => {
        setView((old) => viewReducer(old, { type: "open", tab: surfaceTab(old, "settings") }));
      }}
      onSetMergeMode={live ? () => {} : undefined}
      replayOf={live ? undefined : "scripted fixture"}
    />
  );
}
root.render(<Harness live />);
declare global {
  interface Window {
    settingsReplay: () => void;
  }
}
window.settingsReplay = () => {
  root.render(<Harness key="replay" live={false} />);
};
