import { useLayoutEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import log from "../../../fixtures/events/handwritten-inbox-grants.jsonl?raw";
import { applyEvent, createInitialState, stateStore } from "../state";
import { parseEvent } from "../transport";
import { Frame } from "./Frame";
import type { View } from "./view";
import { clickNode, closePanel, navigateView, surfaceTab, tabbedView, viewReducer } from "./view";
const events = log.trim().split("\n").map(parseEvent);
let root: Root | undefined;
let openChange: (id: string) => void;
function Harness({ end }: { end: number }) {
  const state = events.slice(0, end).reduce(applyEvent, createInitialState());
  const [view, setView] = useState<View>(tabbedView);
  const review = (id: string) => {
    setView((current) =>
      navigateView(current, { ...current, review: id, diff: null, file: null }, state),
    );
  };
  useLayoutEffect(() => {
    openChange = review;
  });
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
      replayLabel="Hand-written inbox and grants fixture"
      replayOf="hand-written fixture"
    />
  );
}
export function mountInbox(end = events.length) {
  root?.unmount();
  stateStore.setState({ state: events.slice(0, end).reduce(applyEvent, createInitialState()) });
  const host = document.createElement("div");
  host.style.height = "100vh";
  document.body.replaceChildren(host);
  root = createRoot(host);
  flushSync(() => root?.render(<Harness end={end} />));
}

export function openInboxChange(id: string) {
  flushSync(() => {
    openChange(id);
  });
}
