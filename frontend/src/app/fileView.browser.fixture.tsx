// Browser-only harness around the real App and its page-owned draft/save session.
import type { ServerEvent } from "@contracts";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import { applyEvent, createInitialState, stateStore } from "../state";
import { App } from "./App";
const events = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);

export const fileViewTest = {
  untouched: "shop.cart:Cart.add",
  holdSlots() {
    const { state } = stateStore.getState();
    if (state.run)
      stateStore.setState({
        state: { ...state, run: { ...state.run, slots: state.run.slots.slice(0, 1) } },
      });
  },
  mount() {
    stateStore.setState({
      state: events.filter((event) => event.seq <= 20).reduce(applyEvent, createInitialState()),
    });
    const host = document.createElement("div");
    host.id = "root";
    host.style.height = "100vh";
    document.body.replaceChildren(host);
    flushSync(() => {
      createRoot(host).render(<App />);
    });
  },
  commit(commit: string) {
    const { state } = stateStore.getState();
    if (state.graph)
      stateStore.setState({ state: { ...state, graph: { ...state.graph, commit } } });
  },
};

declare global {
  interface Window {
    fileViewTest: typeof fileViewTest;
  }
}
