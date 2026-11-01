import { useStore } from "zustand";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { applyEvent, createInitialState, stateStore } from "../../state";
import { replayBuild, replaySource } from "../../transport";
import type { ServerEvent } from "@contracts";
import log from "../../../../fixtures/events/recorded-demo.jsonl?raw";
import { Frame } from "../Frame";
import { closePanel, surfaceTab, tabbedView, viewReducer, type View } from "../view";
import { useEscapeCloses } from "../escape";

const params = new URLSearchParams(location.search);
document.documentElement.dataset.theme = params.get("theme") ?? "dark";
const selectedView = { ...tabbedView, selected: "shop.pricing:compute_price" };
const events = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
function Harness() {
  const state = useStore(stateStore, (store) => store.state);
  const [view, setView] = useState<View>(() =>
    params.has("initial")
      ? viewReducer(selectedView, { type: "open", tab: surfaceTab(selectedView, "usage") })
      : selectedView,
  );
  const close = () => {
    setView(closePanel);
  };
  useEscapeCloses(Boolean(view.activeTab), close);
  return (
    <Frame
      state={state}
      view={view}
      onNodeTap={(id) => {
        setView((view) => ({ ...view, selected: id }));
      }}
      canOpenChange={() => false}
      onOpenChange={() => undefined}
      onClosePanel={close}
      onTabAction={(action) => {
        setView((view) => viewReducer(view, action));
      }}
      onOpenUsage={
        replayBuild
          ? undefined
          : () => {
              setView((view) =>
                viewReducer(view, { type: "open", tab: surfaceTab(view, "usage") }),
              );
            }
      }
      onOpenSettings={() => {
        setView((view) => viewReducer(view, { type: "open", tab: surfaceTab(view, "settings") }));
      }}
      replayOf={params.has("recorded") ? "recorded" : undefined}
      replayLabel={params.has("recorded") ? replaySource.label : undefined}
      connection="connected"
    />
  );
}
const state = events
  .filter((event) => event.seq <= (params.has("early") ? 60 : 69))
  .reduce(applyEvent, createInitialState());
if (params.has("no-run")) state.run = null;
stateStore.setState({ state });
Object.assign(window, {
  usageTest: {
    refresh(runId = state.run?.run_id) {
      const current = stateStore.getState().state;
      stateStore.setState({
        state: {
          ...current,
          lastSeq: current.lastSeq + 1,
          run: current.run ? { ...current.run, run_id: runId ?? "run-next" } : null,
        },
      });
    },
  },
});
const root = document.getElementById("root");
if (!root) throw new Error("Missing root");
createRoot(root).render(<Harness />);
