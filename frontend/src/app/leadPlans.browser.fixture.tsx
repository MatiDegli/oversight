import { useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import log from "../../../fixtures/events/handwritten-lead-plans.jsonl?raw";
import { applyEvent, createInitialState, stateStore } from "../state";
import { parseEvent } from "../transport";
import { Frame } from "./Frame";
import { clickNode, closePanel, navigateView, surfaceTab, tabbedView, viewReducer } from "./view";
const events = log.trim().split("\n").map(parseEvent);
let root: Root | undefined;
function Harness({ end }: { end: number }) {
  const state = events.slice(0, end).reduce(applyEvent, createInitialState());
  const [view, setView] = useState(tabbedView);
  const review = (id: string) => {
    setView((v) => navigateView(v, { ...v, review: id, diff: null, file: null }, state));
  };
  return (
    <Frame
      state={state}
      view={view}
      onNodeTap={(id) => {
        setView((v) => navigateView(v, clickNode(state, v, id), state));
      }}
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
      onTabAction={(action) => {
        setView((v) => viewReducer(v, action));
      }}
      replayLabel="Hand-written three lead plans fixture"
      replayOf="hand-written fixture"
    />
  );
}
export function mountLeadPlans(end = 8) {
  stateStore.setState({ state: events.slice(0, end).reduce(applyEvent, createInitialState()) });
  if (!root) {
    const host = document.createElement("div");
    host.style.height = "100vh";
    document.body.replaceChildren(host);
    root = createRoot(host);
  }
  flushSync(() => root?.render(<Harness end={end} />));
}
