// Mounted only by preview tests via transformed HTML. Never imported by the product.
import { lazy, Suspense, useState, useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import type { ServerEvent } from "@contracts";
import { applyEvent, createInitialState, stateStore } from "../../state";
import { loadReviewPanel } from "../../review";
import { GraphView } from "../../graph";
import recordedLog from "../../../../fixtures/events/recorded-demo.jsonl?raw";
import ordinaryLog from "../../../../fixtures/events/handwritten-level1.jsonl?raw";
import { Frame } from "../Frame";
import { conversationTab, surfaceTab, tabbedView, viewReducer, type View } from "../view";
import { InitialShellLayout } from "../shell/useShell";
import { createPreviewHost } from "./previewHost";
import { createPreviewController } from "./previewController";
import { PreviewPanel } from "./PreviewPanel";
import { fakeCandidate, PreviewFakeClock } from "./previewFake";
import type { PreviewCandidate, PreviewPort } from "./previewPort";
import type { PreviewReason, PreviewStatus } from "./previewTypes";
import { initialOpening, previewOpening } from "./previewOpening";
import type { OpeningGuards, PreviewSurface } from "./previewOpening";

export type FakeRequest =
  | { kind: "start"; input: PreviewCandidate }
  | { kind: "details"; id: string }
  | { kind: "renew" | "stop"; id: string; body: { lease_token: string } }
  | { kind: "public" }
  | { kind: "publish"; state: PreviewStatus["state"]; reason: PreviewReason | null };
// A call-through boundary only. No token value is assigned to a fixture global.
declare global {
  interface Window {
    previewBoundary: (request: FakeRequest) => Promise<unknown>;
    previewFixture: typeof api;
  }
}
const port: PreviewPort = {
  start: (input) =>
    window.previewBoundary({ kind: "start", input }) as ReturnType<PreviewPort["start"]>,
  details: (id) =>
    window.previewBoundary({ kind: "details", id }) as ReturnType<PreviewPort["details"]>,
  renew: (id, body) =>
    window.previewBoundary({ kind: "renew", id, body }) as ReturnType<PreviewPort["renew"]>,
  stop: (id, body) =>
    window.previewBoundary({ kind: "stop", id, body }) as ReturnType<PreviewPort["stop"]>,
};
const localClock = new PreviewFakeClock();
const controller = createPreviewController(port, localClock, {
  run: fakeCandidate.run_id,
  source: "fake-live",
});
let candidate = { ...fakeCandidate };
let ready = false;
let preference = false;
const connection = { run: fakeCandidate.run_id, source: "fake-live", generation: 1 };
let opening = previewOpening(initialOpening, { type: "reset", connection }).state;
opening = previewOpening(opening, { type: "fence", connection, seq: 10, committed: true }).state;
opening = previewOpening(opening, { type: "prefix", connection, seq: 20, contiguous: true }).state;
let finishRevision = 0;
const api = {
  ready: () => ready,
  advance: (milliseconds: number) => {
    localClock.advance(milliseconds);
  },
  snapshot: controller.snapshot,
  async see(next?: Partial<PreviewCandidate>) {
    candidate = { ...candidate, ...next };
    await controller.see(() => candidate);
  },
  candidate(next: PreviewCandidate) {
    candidate = next;
  },
  async observePublic() {
    const status = (await window.previewBoundary({ kind: "public" })) as PreviewStatus | null;
    if (status) controller.observe(status);
  },
  async publish(state: PreviewStatus["state"], reason: PreviewReason | null) {
    const status = (await window.previewBoundary({
      kind: "publish",
      state,
      reason,
    })) as PreviewStatus;
    controller.observe(status);
    await controller.refresh();
  },
  refresh: controller.refresh,
  navigate: controller.navigate,
  disappear: controller.dispose,
  finish(surface: PreviewSurface, patch: Partial<OpeningGuards> = {}) {
    const result = previewOpening(opening, {
      type: "finish",
      connection,
      seq: 20,
      live: true,
      genuinelyNew: true,
      candidate: { ...fakeCandidate, revision: ++finishRevision },
      guards: {
        surface,
        configured: true,
        preference,
        targetVisible: true,
        allocatedSlot: false,
        openReview: false,
        openPreview: false,
        layoutUnchanged: true,
        tabUnchanged: true,
        framingUnchanged: true,
        focusUnchanged: true,
        scrollUnchanged: true,
        ...patch,
      },
    });
    opening = result.state;
    // No start/navigation connection. Even the fixture preference offers See preview only.
    return result.action;
  },
};
window.previewFixture = api;
window.addEventListener("pagehide", controller.dispose);
const params = new URLSearchParams(location.search);
document.documentElement.dataset.theme = params.get("theme") ?? "dark";
const mode = params.get("mode") ?? "panel";
const scenario = params.get("scenario") ?? "ready";
const Review = lazy(() => loadReviewPanel().then((module) => ({ default: module.ReviewPanel })));

function PreviewFixture() {
  const view = useSyncExternalStore(controller.subscribe, controller.snapshot);
  const [enabled, enable] = useState(false);
  return (
    <main className="preview-fixture">
      <div className="preview-fixture-caption">Isolated fixture · handwritten fake authority</div>
      <div className="preview-fixture-controls">
        {(!view.visible || view.owned) && (
          <button
            type="button"
            onClick={() => {
              void api.see({ agent_id: "agent-b" });
            }}
          >
            See preview
          </button>
        )}
        {view.owned && (
          <button
            type="button"
            onClick={() => {
              void controller.navigate();
            }}
          >
            Leave preview
          </button>
        )}
        <label>
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => {
              enable(event.target.checked);
              preference = event.target.checked;
            }}
          />
          Open preview when done
        </label>
      </div>
      <div className="preview-fixture-slot">
        <PreviewPanel controller={controller} />
      </div>
    </main>
  );
}
function eventState(log: string, through: number) {
  return log
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as ServerEvent)
    .filter((event) => event.seq <= through)
    .reduce(applyEvent, createInitialState());
}
const shellState = eventState(recordedLog, 69);
const reviewState = eventState(ordinaryLog, 20);
stateStore.setState({ state: mode === "review" ? reviewState : shellState });
function initialView(): View {
  const surface = params.get("surface") ?? mode;
  if (["usage", "settings", "inbox"].includes(surface))
    return viewReducer(tabbedView, {
      type: "open",
      tab: surfaceTab(tabbedView, surface as "usage" | "settings" | "inbox"),
    });
  if (surface === "oversight-watch") return tabbedView;
  return viewReducer(tabbedView, {
    type: "open",
    tab: conversationTab(tabbedView, shellState, "agent-1"),
  });
}
// Frame geometry fixture only: explicit see() is the test's activation, never a product arrival.
const geometryHost = {
  ...createPreviewHost(
    port,
    localClock,
    { run: fakeCandidate.run_id, source: "fixture" },
    () => shellState,
    () => false,
  ),
  controller,
  subscribe: controller.subscribe,
  snapshot: () => controller.snapshot().visible,
};
function BuiltShell() {
  const [view, setView] = useState(initialView);
  return (
    <InitialShellLayout.Provider
      value={params.get("surface")?.startsWith("chat") ? "Chat" : "Oversight"}
    >
      <Frame
        previewHost={params.get("slot") === "preview" ? geometryHost : undefined}
        state={shellState}
        view={view}
        onNodeTap={() => undefined}
        canOpenChange={() => false}
        onOpenChange={() => undefined}
        onClosePanel={() => {
          setView(tabbedView);
        }}
        onTabAction={(action) => {
          setView((current) => viewReducer(current, action));
        }}
        onOpenUsage={() => {
          setView((current) =>
            viewReducer(current, { type: "open", tab: surfaceTab(current, "usage") }),
          );
        }}
        onOpenSettings={() => {
          setView((current) =>
            viewReducer(current, { type: "open", tab: surfaceTab(current, "settings") }),
          );
        }}
        replayOf="handwritten fixture"
        connection="connected"
      />
    </InitialShellLayout.Provider>
  );
}
function BuiltReview() {
  return (
    <main className="preview-fixture-reference">
      <div className="preview-fixture-reference-graph" aria-hidden="true">
        <GraphView />
      </div>
      <Suspense fallback={<p>Loading review…</p>}>
        <Review state={reviewState} changeId="change-1" onClose={() => undefined} />
      </Suspense>
    </main>
  );
}
async function mountScenario() {
  if (mode !== "panel") {
    ready = true;
    return;
  }
  const navigation = performance.getEntriesByType("navigation")[0] as
    PerformanceNavigationTiming | undefined;
  if (
    navigation?.type === "reload" ||
    scenario === "observer" ||
    scenario === "isolation-before-launch"
  )
    await api.observePublic();
  else {
    await api.see(params.get("agent") === "agent-b" ? { agent_id: "agent-b" } : undefined);
    if (scenario === "stopping-closed") {
      void controller.close();
      await Promise.resolve();
      await api.observePublic();
    } else if (scenario === "failed-stop") await api.see({ agent_id: "agent-b" });
    else if (scenario.startsWith("failed-"))
      await api.publish("failed", scenario.slice(7) as PreviewReason);
    else if (scenario.startsWith("stopped-"))
      await api.publish("stopped", scenario.slice(8) as PreviewReason);
  }
  ready = true;
}
const root = document.getElementById("root");
if (!root) throw new Error("Missing preview fixture root");
createRoot(root).render(
  mode === "panel" ? <PreviewFixture /> : mode === "review" ? <BuiltReview /> : <BuiltShell />,
);
void mountScenario();
