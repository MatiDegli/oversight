import type { AgentStatus, ServerEvent } from "@contracts";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import log from "../../../../fixtures/events/handwritten-level1.jsonl?raw";
import { Frame } from "../Frame";
import { initialView } from "../view";
import { applyEvent, createInitialState } from "../../state";

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
let commands = true;
let pauseFails = false;
const pauseCalls: string[] = [];
const resumeCalls: string[] = [];
function render() {
  flushSync(() => {
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
        onStopAgent={commands ? () => Promise.resolve() : undefined}
        onPauseAgent={
          commands
            ? (id) => {
                pauseCalls.push(id);
                return pauseFails ? Promise.reject(new Error("offline")) : Promise.resolve();
              }
            : undefined
        }
        onResumeAgent={
          commands
            ? (id) => {
                resumeCalls.push(id);
                return Promise.resolve();
              }
            : undefined
        }
      />,
    );
  });
}
export const readyTest = {
  mount(withCommands = true) {
    commands = withCommands;
    render();
  },
  pauseCalls,
  resumeCalls,
  failPause() {
    pauseFails = true;
  },
  status(next: AgentStatus["state"]) {
    const agent = state.agents["agent-1"];
    if (!agent) throw new Error("Missing agent");
    state = applyEvent(state, {
      seq: state.lastSeq + 1,
      ts: events[0]?.ts ?? "",
      type: "agent.status",
      payload: { ...agent, state: next },
    });
    render();
  },
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
