import type { ServerEvent } from "@contracts";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import log from "../../../fixtures/events/handwritten-measured-cone.jsonl?raw";
import { applyEvent, createInitialState, stateStore } from "../state";
import { Frame } from "./Frame";
import { initialView } from "./view";

const events = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
let state = createInitialState();
const host = document.createElement("div");
host.style.height = "100vh";
document.body.replaceChildren(host);
const root = createRoot(host);
const idle = () => undefined;
function render() {
  stateStore.setState({ state });
  flushSync(() => {
    root.render(
      <Frame
        state={state}
        view={state.lastSeq < 19 ? initialView : { ...initialView, review: "change-1" }}
        onNodeTap={idle}
        canOpenChange={() => true}
        canReview={() => true}
        onOpenChange={idle}
        onReview={idle}
        onClosePanel={idle}
        onDecide={idle}
        replayLabel="Hand-written measured caller fixture"
        replayOf="hand-written measured caller fixture"
      />,
    );
  });
}
export const measuredConeTest = {
  mount(seq = 21) {
    state = events.filter((e) => e.seq <= seq).reduce(applyEvent, createInitialState());
    render();
  },
  advance(seq: number) {
    state = events.filter((e) => e.seq > state.lastSeq && e.seq <= seq).reduce(applyEvent, state);
    render();
  },
};
declare global {
  interface Window {
    measuredConeTest: typeof measuredConeTest;
  }
}
