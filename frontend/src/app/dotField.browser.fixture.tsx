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
import twoAgents from "../../../fixtures/events/scripted-level2.jsonl?raw";

const CHANGED = "shop.pricing:compute_price";

const parse = (raw: string) =>
  raw
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as ServerEvent);
const events = parse(log);
const both = parse(twoAgents);

const host = document.createElement("div");
host.style.height = "100vh";
document.body.replaceChildren(host);
const root = createRoot(host);

type Scene = "watch" | "live-diff" | "review" | "waiting" | "rest" | "both";

/**
 * The recorded level 1 run: the agent edits at 13, has its change live at 14, proposes at 19 and
 * waits at 20, with the proposal's entry open in the decisions inbox ("waiting" and "review": the
 * change waits on it), the person approves at 21, and the agent is done at 23 ("rest").
 */
const sceneAt = {
  watch: 13,
  "live-diff": 14,
  review: 20,
  waiting: 20,
  rest: 23,
  // The two-agent run at 33: agent B edits restock, agent A edits apply_coupon.
  both: 33,
} satisfies Record<Scene, number>;

const stateAt = (seq: number, from: readonly ServerEvent[] = events) =>
  from.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());

function viewOf(scene: Scene): View {
  const state = stateStore.getState().state;
  const next =
    scene === "live-diff"
      ? openChange(state, initialView, "change-1")
      : scene === "review"
        ? openReview(state, initialView, "change-1")
        : initialView;
  if (!next) throw new Error(`The recorded run cannot open ${scene}`);
  return next;
}

/** The view the frame shows, kept so that later events render on the same screen. */
let shown: View = initialView;
/** The recorded run the mounted scene belongs to: later events come from the same one. */
let run: readonly ServerEvent[] = events;

function show(state: ReturnType<typeof stateAt>, view: View) {
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
    run = scene === "both" ? both : events;
    const state = stateAt(sceneAt[scene], run);
    stateStore.setState({ state });
    shown = viewOf(scene);
    show(state, shown);
  },
  /** Applies the recorded events up to `seq`, as live ones would: same screen, field still mounted. */
  advance(seq: number) {
    const state = stateAt(seq, run);
    stateStore.setState({ state });
    show(state, shown);
  },
};

declare global {
  interface Window {
    fieldTest: typeof fieldTest;
  }
}
