import { createRoot } from "react-dom/client";
import { stateStore } from "../state";
import { parseEvent } from "../transport";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { App } from "./App";

stateStore.getState().applyEvents(
  log
    .trim()
    .split("\n")
    .map(parseEvent)
    .filter((e) => e.seq <= 20),
);
const state = stateStore.getState().state;
if (state.run)
  stateStore.setState({
    state: { ...state, run: { ...state.run, merge_mode: { mode: "ask_me", revision: 0 } } },
  });
Object.assign(window, { appStore: stateStore });
const root = document.getElementById("root");
if (!root) throw new Error("Missing fixture root");
createRoot(root).render(<App />);
