// Hand-written evidence only; no model or transport is started.
import type { ServerEvent } from "@contracts";
import { useCallback, useState } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { useStore } from "zustand";
import behavior from "../../../fixtures/events/handwritten-behavior-review.jsonl?raw";
import measured from "../../../fixtures/events/handwritten-measured-cone.jsonl?raw";
import { applyEvent, createInitialState, stateStore } from "../state";
import { Frame } from "./Frame";
import { useEscapeCloses } from "./escape";
import { closePanel, initialView, openReview, panelOpen } from "./view";

const logs = [behavior, measured].map((log) =>
  log
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as ServerEvent),
);
const host = document.createElement("div");
host.style.height = "100vh";
document.body.replaceChildren(host);
const root = createRoot(host);
function Harness() {
  const state = useStore(stateStore, (store) => store.state);
  const [view, setView] = useState(initialView);
  const close = useCallback(() => {
    setView(closePanel);
  }, []);
  useEscapeCloses(panelOpen(view), close);
  return (
    <Frame
      state={state}
      view={view}
      onNodeTap={() => undefined}
      canOpenChange={() => true}
      canReview={() => true}
      onOpenChange={() => undefined}
      onReview={(id) => {
        setView((current) => openReview(state, current, id) ?? current);
      }}
      onClosePanel={close}
      replayOf="hand-written behavior fixture"
    />
  );
}
export const nodeChipTest = {
  mount(seq = 20, measured = false) {
    const state = (logs[measured ? 1 : 0] ?? [])
      .filter((event) => event.seq <= seq)
      .reduce(applyEvent, createInitialState());
    stateStore.setState({ state });
    flushSync(() => {
      root.render(<Harness />);
    });
  },
  amend(mode: "long" | "empty" | "folded" | "gone" | "two") {
    const state = structuredClone(stateStore.getState().state);
    const change = state.changes["change-1"],
      facts = state.changeFacts["change-1"]?.at(-1);
    const evidence = facts?.behavior_diff?.[0];
    const agent = state.agents["agent-1"];
    if (!change?.proposal || !facts || !evidence || !agent) throw new Error("Missing fixture");
    if (mode === "long")
      evidence.rows = evidence.rows.map((row) => ({
        ...row,
        inputs: `compute_price(${"x".repeat(240)})`,
      }));
    if (mode === "empty") evidence.rows = [];
    if (mode === "folded") evidence.node_id = "shop.cart:Cart.total";
    if (mode === "gone") evidence.node_id = "removed.module:old_function";
    if (mode === "two") {
      state.agents["agent-2"] = { ...agent, agent_id: "agent-2", slot: "B" };
      state.changes["change-2"] = {
        ...change,
        change_id: "change-2",
        agent_id: "agent-2",
        proposal: { ...change.proposal, change_id: "change-2", agent_id: "agent-2" },
      };
      state.changeFacts["change-2"] = [{ ...facts, change_id: "change-2" }];
    }
    flushSync(() => {
      stateStore.setState({ state });
    });
  },
  emit(seq: number) {
    const event = logs[0]?.find((event) => event.seq === seq);
    if (!event) throw new Error("Missing fixture event");
    flushSync(() => {
      stateStore.setState(({ state }) => ({
        state: applyEvent(state, { ...event, seq: state.lastSeq + 1 }),
      }));
    });
  },
};
declare global {
  interface Window {
    nodeChipTest: typeof nodeChipTest;
  }
}
