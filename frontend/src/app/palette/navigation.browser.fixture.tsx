import type { ServerEvent } from "@contracts";
import { useState } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import type { Root } from "react-dom/client";
import log from "../../../../fixtures/events/handwritten-level1.jsonl?raw";
import { applyEvent, createInitialState, stateStore } from "../../state";
import { FilesProvider } from "../FilesContext";
import { Frame } from "../Frame";
import { clickNode, closePanel, navigateView, tabbedView, viewReducer } from "../view";
import type { View } from "../view";
import { createRecordedFiles } from "../recordedFiles";

const events = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
let root: Root | undefined;
function Harness({ seq, recorded }: { seq: number; recorded: boolean }) {
  const [state] = useState(() =>
    events.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState()),
  );
  const [view, setView] = useState<View>(tabbedView);
  const files = createRecordedFiles(() => state);
  return (
    <FilesProvider
      state={state}
      openFile={(path) => {
        const module = state.graph?.nodes.find(
          (node) => node.kind === "module" && node.path === path,
        );
        setView((current) =>
          navigateView(
            current,
            {
              selected: module?.id ?? null,
              diff: null,
              review: null,
              file: { path, agentId: null, startLine: 1, endLine: module?.end_line ?? 1 },
            },
            state,
          ),
        );
      }}
    >
      <Frame
        state={state}
        view={view}
        files={files}
        onNodeTap={(id) => {
          setView((current) =>
            navigateView(current, clickNode(state, current, id, !recorded), state),
          );
        }}
        canOpenChange={() => false}
        onOpenChange={() => undefined}
        onClosePanel={() => {
          setView(closePanel);
        }}
        onTabAction={(action) => {
          setView((current) => viewReducer(current, action));
        }}
        onStartAgent={recorded ? undefined : () => Promise.resolve()}
        replayOf={recorded ? "hand-written demo fixture" : undefined}
      />
    </FilesProvider>
  );
}
export const paletteNavigation = {
  mount(seq: number, recorded = false) {
    root?.unmount();
    stateStore.setState({
      state: events.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState()),
    });
    const host = document.createElement("div");
    host.style.height = "100vh";
    document.body.replaceChildren(host);
    root = createRoot(host);
    flushSync(() => {
      root?.render(<Harness seq={seq} recorded={recorded} />);
    });
  },
};
declare global {
  interface Window {
    paletteNavigation: typeof paletteNavigation;
  }
}
