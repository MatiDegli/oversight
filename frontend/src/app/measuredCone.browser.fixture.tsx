import type { ServerEvent } from "@contracts";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import log from "../../../fixtures/events/handwritten-measured-cone.jsonl?raw";
import { applyEvent, createInitialState, stateStore } from "../state";
import { Frame } from "./Frame";
import { initialView } from "./view";
// eslint-disable-next-line no-restricted-imports -- Observe the renderer's SVGs in this test harness only.
import { imageSource } from "../graph/layerImages";

const drawnWords = new Set<string>();
for (const prototype of [
  CanvasRenderingContext2D.prototype,
  OffscreenCanvasRenderingContext2D.prototype,
]) {
  // eslint-disable-next-line @typescript-eslint/unbound-method -- The proxy forwards the native receiver unchanged.
  prototype.drawImage = new Proxy(prototype.drawImage, {
    apply(target, context: unknown, args: unknown[]) {
      const source = args[0];
      if (source instanceof HTMLImageElement) {
        const svg = decodeURIComponent(imageSource(source.src));
        for (const word of ["differs", "same", "varied", "not measured"])
          if (svg.includes(`>${word}</text>`)) drawnWords.add(word);
      }
      Reflect.apply(target, context, args);
    },
  });
}

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
    drawnWords.clear();
    state = events.filter((e) => e.seq > state.lastSeq && e.seq <= seq).reduce(applyEvent, state);
    render();
  },
  drawnWords: () => [...drawnWords].sort(),
};
declare global {
  interface Window {
    measuredConeTest: typeof measuredConeTest;
  }
}
