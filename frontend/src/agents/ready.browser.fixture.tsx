import type { ServerEvent } from "@contracts";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { Frame } from "../app/Frame";
import { initialView } from "../app/view";
import { applyEvent, createInitialState } from "../state";

const events = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
let state = events.filter((event) => event.seq <= 18).reduce(applyEvent, createInitialState());
const a = state.agents["agent-1"];
if (!a || !state.run) throw new Error("Missing agent");
state = {
  ...state,
  run: { ...state.run, slots: [...state.run.slots, { slot: "B", default_model: a.model }] },
  agents: {
    ...state.agents,
    "agent-2": { ...a, agent_id: "agent-2", slot: "B", change_id: "change-2" },
  },
};
const host = document.createElement("div");
host.style.height = "100vh";
document.body.replaceChildren(host);
const root = createRoot(host);
function render() {
  flushSync(() =>
    root.render(
      <Frame
        state={state}
        view={initialView}
        onNodeTap={() => undefined}
        canOpenChange={() => true}
        canReview={(id) => state.changes[id]?.status === "ready_for_review"}
        onOpenChange={() => undefined}
        onReview={() => undefined}
        onClosePanel={() => undefined}
        onStartAgent={() => Promise.resolve()}
        onStopAgent={() => Promise.resolve()}
      />,
    ),
  );
}
export const readyTest = {
  mount: render,
  ready() {
    state = events.filter((event) => event.seq > 18 && event.seq <= 20).reduce(applyEvent, state);
    render();
  },
};
declare global {
  interface Window {
    readyTest: typeof readyTest;
  }
}
