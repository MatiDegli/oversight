// Browser-only test harness; never imported by the application.
import type { ServerEvent } from "@contracts";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import { Frame } from "./Frame";
import { initialView, openChange } from "./view";
import type { View } from "./view";
import { applyEvent, createInitialState, stateStore } from "../state";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";

const CHANGED = "shop.pricing:compute_price";

// The recorded level 1 run up to the change being pending, with a layout of our own: a wide grid
// with the changed function at its far right, where a narrower graph cuts it off.
const events = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent)
  .filter((event) => event.seq <= 14);
const recorded = events.reduce(applyEvent, createInitialState());
if (!recorded.graph) throw new Error("The recorded run has no graph");
const positions = Object.fromEntries(
  recorded.graph.nodes.map((node, index) => [
    node.id,
    { x: (index % 10) * 170, y: Math.floor(index / 10) * 90 },
  ]),
);
positions[CHANGED] = { x: 1900, y: 200 };
// This camera case has a single changed node with no callers. The real live-cone case is
// covered separately in dotField.browser.test.ts with the replay's original call edges.
stateStore.setState({ state: { ...recorded, graph: { ...recorded.graph, positions, edges: [] } } });

const host = document.createElement("div");
host.style.height = "100vh";
document.body.replaceChildren(host);
const root = createRoot(host);

function render(view: View) {
  const state = stateStore.getState().state;
  flushSync(() => {
    root.render(
      <Frame
        state={state}
        view={view}
        onNodeTap={() => undefined}
        canOpenChange={() => false}
        onOpenChange={() => undefined}
        onClosePanel={() => undefined}
      />,
    );
  });
}

export const graphTest = {
  changed: CHANGED,
  mount() {
    render(initialView);
  },
  /** Opens the card's Live diff, as the person would; the camera should follow. */
  open() {
    const next = openChange(stateStore.getState().state, initialView, "change-1");
    if (!next) throw new Error("The change cannot be opened");
    render(next);
  },
  close() {
    render(initialView);
  },
};

declare global {
  interface Window {
    graphTest: typeof graphTest;
  }
}
