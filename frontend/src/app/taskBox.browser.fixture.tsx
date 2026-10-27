// Browser-only test harness; never imported by the application.
import type { ServerEvent, StartAgent } from "@contracts";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { applyEvent, createInitialState, stateStore } from "../state";
import { twoSlotState } from "../taskBox.fixture";
import { CommandError } from "../transport";
import { Frame } from "./Frame";
import { initialView, openChange } from "./view";
import type { View } from "./view";

const events = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);

// The server announces slot A only in level 1: one running agent leaves no slot free.
const opening = events.filter((event) => event.seq <= 2).reduce(applyEvent, createInitialState());
if (!opening.run) throw new Error("The recorded run starts with run.started");
stateStore.setState({
  state: { ...opening, run: { ...opening.run, slots: opening.run.slots.slice(0, 1) } },
});

const host = document.createElement("div");
host.style.height = "100vh";
document.body.replaceChildren(host);
const root = createRoot(host);

let view: View = initialView;
const calls: StartAgent[] = [];
let answer: { resolve: () => void; reject: (error: unknown) => void } | undefined;
const start = (body: StartAgent) =>
  new Promise<undefined>((resolve, reject) => {
    calls.push(body);
    answer = {
      resolve: () => {
        resolve(undefined);
      },
      reject,
    };
  });

function render() {
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
        onStartAgent={start}
      />,
    );
  });
}

export type StartAnswer = "created" | "network" | { status: number; code: string; message: string };

export const taskBoxTest = {
  mount() {
    stateStore.subscribe(render);
    render();
  },
  calls: () => structuredClone(calls),
  /** Offer both profiles, optionally after the non-Meta tick carrying Meta's brake. */
  twoSlots(braked = false) {
    stateStore.setState({ state: twoSlotState(braked) });
  },
  /** Reuse recorded A events after the slot test's opening, then render from the same store. */
  holdA() {
    const agent = events.find((event) => event.type === "agent.status");
    if (!agent) throw new Error("Missing agent status");
    stateStore.getState().applyEvents([
      {
        ...agent,
        seq: stateStore.getState().state.lastSeq + 1,
        payload: { ...agent.payload, model: "muse-spark", state: "working" },
      },
    ]);
  },
  /** Answers the start request that is waiting: 201, a refusal, or a lost connection. */
  answer(result: StartAnswer) {
    if (!answer) throw new Error("No start is waiting");
    const waiting = answer;
    answer = undefined;
    if (result === "created") waiting.resolve();
    else if (result === "network") waiting.reject(new TypeError("Failed to fetch"));
    else {
      const { status, code, message } = result;
      waiting.reject(new CommandError(status, code as never, message));
    }
  },
  /** Applies the recorded events up to `seq`, as the SSE stream would deliver them. */
  apply(seq: number) {
    const last = stateStore.getState().state.lastSeq;
    stateStore
      .getState()
      .applyEvents(events.filter((event) => event.seq > last && event.seq <= seq));
  },
  /** Opens the live diff, which folds the agents panel into the rail and unmounts the box. */
  fold() {
    const next = openChange(stateStore.getState().state, initialView, "change-1");
    if (!next) throw new Error("The change cannot be opened");
    view = next;
    render();
  },
  unfold() {
    view = initialView;
    render();
  },
};

declare global {
  interface Window {
    taskBoxTest: typeof taskBoxTest;
    boxNode?: Element | null;
  }
}
