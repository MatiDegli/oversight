// Browser-only test harness; never imported by the application.
import type { GraphSnapshot, ServerEvent } from "@contracts";
import { useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import { GraphView } from "../graph";
import type { Framing, GraphApi } from "../graph";
import { applyEvent, createInitialState, stateStore } from "../state";
import { GraphHeading, Legend, ZoomControls } from "./GraphOverlays";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import demoGraph from "../../../fixtures/graph/demo-repo.json";
import demoPreset from "../../../fixtures/layouts/demo-repo.json";

const CHANGED = "shop.pricing:compute_price";
const CALLER = "shop.cart:Cart.quote_add";
const events = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent)
  .filter((event) => event.seq <= 20);
const recorded = events.reduce(applyEvent, createInitialState());
if (!recorded.graph) throw new Error("The fixture has no graph");
const proposal = recorded.changes["change-1"]?.proposal;
if (!proposal) throw new Error("The fixture has no cone");
const cone = proposal.impact;
const nodes = recorded.graph.nodes;
let preview = cone;
// A wide preset with the changed function far right and a caller below the initial review view.
const positions = Object.fromEntries(
  nodes.map((node, index) => [node.id, { x: (index % 10) * 170, y: Math.floor(index / 10) * 90 }]),
);
positions[CHANGED] = { x: 1900, y: 200 };
for (const node of nodes.filter((n) => n.parent_id === "shop.cart:Cart")) {
  positions[node.id] = { x: 1900, y: 2500 + node.start_line * 3 };
}
stateStore.setState({ state: { ...recorded, graph: { ...recorded.graph, positions } } });

const host = document.createElement("div");
host.style.height = "100vh";
document.body.replaceChildren(host);
const root = createRoot(host);
type Mode = "watch" | "panel" | "review";
let ImpactConeCard: typeof import("../review/ImpactCone").ImpactConeCard;
async function loadCone() {
  // eslint-disable-next-line no-restricted-syntax -- Browser-only isolation: the review barrel also loads Monaco.
  ({ ImpactConeCard } = await import("../review/ImpactCone"));
}

/** Exercise the camera without importing Frame's unrelated Monaco editor and workers. */
function Harness({ mode }: { mode: Mode }) {
  const api = useRef<GraphApi | null>(null);
  const card = useRef<HTMLDivElement>(null);
  const startArea = useRef<HTMLDivElement>(null);
  const [framing, setFraming] = useState<Framing | null>(null);
  const impact = mode === "review" ? cone : null;
  const litIds = useMemo(
    () => (mode !== "watch" ? [CHANGED, ...cone.entries.map((entry) => entry.node_id)] : null),
    [mode],
  );
  return (
    <div className="flex h-full bg-ground text-text">
      {mode !== "watch" && <aside aria-label="Panel" className="w-[700px] shrink-0" />}
      <main className="relative min-w-0 flex-1">
        <GraphView
          panelOpen={mode !== "watch"}
          frameIds={[CHANGED]}
          frameKey={mode}
          cone={impact}
          liveCone={mode === "panel" ? preview : null}
          litIds={litIds}
          onFramed={setFraming}
          coneCardRef={card}
          apiRef={api}
          startAreaRef={startArea}
        />
        <div className="pointer-events-none absolute inset-6 flex flex-col gap-(--spacing-md)">
          <div className="flex min-h-0 flex-col items-start gap-(--spacing-md)">
            {impact ? (
              <div ref={card} className="pointer-events-auto w-(--cone-card-width)">
                <ImpactConeCard
                  cone={impact}
                  nodes={nodes}
                  outOfView={framing?.outOfView}
                  onReveal={(id) => api.current?.reveal(id)}
                />
              </div>
            ) : mode === "watch" ? (
              <GraphHeading state={stateStore.getState().state} />
            ) : null}
          </div>
          <div data-graph-clear-area ref={startArea} className="min-h-0 flex-1" />
          <div className="flex items-end justify-between gap-(--spacing-lg)">
            <div className="pointer-events-auto min-w-0">
              <Legend state={stateStore.getState().state} />
            </div>
            <div className="pointer-events-auto">
              <ZoomControls api={api} />
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

function render(mode: Mode) {
  flushSync(() => {
    root.render(<Harness mode={mode} />);
  });
}

/** Isolated component: a reveal updates its visibility prop, with another focus target nearby. */
async function mountCone(away = true) {
  await loadCone();
  flushSync(() => {
    root.render(
      <>
        <button type="button">Other control</button>
        <ImpactConeCard
          cone={{ ...cone, entries: [{ node_id: CALLER, distance: 1 }] }}
          nodes={nodes}
          outOfView={new Set(away ? [CALLER] : [])}
          onReveal={() => {
            void mountCone(false);
          }}
        />
      </>,
    );
  });
}

export const graphTest = {
  changed: CHANGED,
  caller: CALLER,
  mount: () => {
    render("watch");
  },
  mountEmpty() {
    stateStore.setState({ state: createInitialState() });
    render("watch");
  },
  loadDemo() {
    stateStore.setState({
      state: { ...recorded, graph: { ...demoGraph, positions: demoPreset } as GraphSnapshot },
    });
    render("watch");
  },
  open: () => {
    render("panel");
  },
  review: async () => {
    await loadCone();
    render("review");
  },
  close: () => {
    render("watch");
  },
  update() {
    preview = { ...cone, entries: cone.entries.filter((entry) => entry.distance === 1) };
    stateStore.setState({ state: { ...stateStore.getState().state } });
    render("panel");
  },
  mountCone,
};

declare global {
  interface Window {
    graphTest: typeof graphTest;
  }
}
