// Browser-only test harness; never imported by the application.
import type { ServerEvent } from "@contracts";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import type { FieldProbe, GraphProbeHost } from "../graph";
import { Frame } from "./Frame";
import { initialView, openChange, openReview } from "./view";
import type { View } from "./view";
import { applyEvent, createInitialState, stateStore } from "../state";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";

const CHANGED = "shop.pricing:compute_price";

const events = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);

const host = document.createElement("div");
host.style.height = "100vh";
document.body.replaceChildren(host);
const root = createRoot(host);

type Scene = "watch" | "live-diff" | "review";

/** The recorded level 1 run: the agent edits at 13, has its change live at 14, proposes at 20. */
const sceneAt = { watch: 13, "live-diff": 14, review: 20 } satisfies Record<Scene, number>;

function viewOf(scene: Scene): View {
  const state = stateStore.getState().state;
  const next =
    scene === "watch"
      ? initialView
      : scene === "live-diff"
        ? openChange(state, initialView, "change-1")
        : openReview(state, initialView, "change-1");
  if (!next) throw new Error(`The recorded run cannot open ${scene}`);
  return next;
}

export const fieldTest = {
  changed: CHANGED,
  /** The dot field's canvas is the first child of the graph's wrapper; the graph element follows. */
  elements(): {
    graph: GraphProbeHost;
    field: HTMLCanvasElement & { oversightField?: FieldProbe };
  } {
    const graph = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const field = graph?.parentElement?.firstElementChild;
    if (!graph || !(field instanceof HTMLCanvasElement)) throw new Error("No dot field");
    return { graph, field };
  },
  /** Mounts the real frame on the recorded run as it stands for a scene, with no transport. */
  mount(scene: Scene) {
    const state = events
      .filter((event) => event.seq <= sceneAt[scene])
      .reduce(applyEvent, createInitialState());
    stateStore.setState({ state });
    const view = viewOf(scene);
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
  },
};

declare global {
  interface Window {
    fieldTest: typeof fieldTest;
  }
}
