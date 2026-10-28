import { useLayoutEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import type { FileAccess } from "../editor";
import { Frame } from "./Frame";
import { useEscapeCloses } from "./escape";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import type { ServerEvent } from "@contracts";
import { applyEvent, createInitialState, stateStore } from "../state";
const events = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const through = (seq: number) =>
  events.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());
import {
  clickNode,
  closePanel,
  navigateView,
  openChange,
  openReview,
  panelOpen,
  tabbedView,
  viewReducer,
} from "./view";
import type { View } from "./view";

const files: FileAccess = {
  drafts: new Map(),
  inFlightSaves: new Map(),
  saved: new Map(),
  readFile: (path, agent) =>
    Promise.resolve({
      path,
      agent_id: agent ?? null,
      content: "def example():\n    return 1\n",
      hash: "h1",
    }),
  saveFile: (body) => Promise.resolve({ path: body.path, hash: "h2" }),
};
let navigate: (kind: "file" | "diff" | "review") => void;
let update: () => void;
function Harness() {
  const [state, setState] = useState(through(14));
  const [view, setView] = useState<View>(tabbedView);
  const close = () => {
    setView(closePanel);
  };
  useEscapeCloses(panelOpen(view), close);
  const open = (kind: "file" | "diff" | "review") => {
    setView((current) =>
      navigateView(
        current,
        kind === "file"
          ? {
              selected: "shop.cart:Cart.add",
              diff: null,
              review: null,
              file: { path: "shop/cart.py", agentId: null, startLine: 1, endLine: 2 },
            }
          : ((kind === "diff"
              ? openChange(state, current, "change-1")
              : openReview(through(20), current, "change-1")) ?? current),
        state,
      ),
    );
  };
  useLayoutEffect(() => {
    navigate = open;
    update = () => {
      const next = through(20);
      stateStore.setState({ state: next });
      setState(next);
    };
  });
  return (
    <Frame
      state={state}
      view={view}
      files={files}
      onNodeTap={(id) => {
        setView((current) => navigateView(current, clickNode(state, current, id, true), state));
      }}
      canOpenChange={() => true}
      onOpenChange={() => {
        open("diff");
      }}
      onReview={() => {
        open("review");
      }}
      onClosePanel={close}
      onTabAction={(action) => {
        setView((current) => viewReducer(current, action));
      }}
      onStartAgent={() => Promise.resolve()}
    />
  );
}
export const shellTest = {
  mount() {
    stateStore.setState({ state: through(14) });
    const host = document.createElement("div");
    host.style.height = "100vh";
    document.body.replaceChildren(host);
    flushSync(() => {
      createRoot(host).render(<Harness />);
    });
  },
  open: (kind: "file" | "diff" | "review") => {
    flushSync(() => {
      navigate(kind);
    });
  },
  update: () => {
    flushSync(update);
  },
};
declare global {
  interface Window {
    shellTest: typeof shellTest;
  }
}
