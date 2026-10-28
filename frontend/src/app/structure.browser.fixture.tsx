// Graph-only browser harness: synchronous reducer, no transport or paid providers.
import { useStore } from "zustand";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import type { GraphSnapshot, ServerEvent } from "@contracts";
import { GraphStage } from "./shell/GraphStage";
import { tabbedView } from "./view";
import { applyEvent, createInitialState, stateStore } from "../state";
import demo from "../../../fixtures/graph/demo-repo.json";
import preset from "../../../fixtures/layouts/demo-repo.json";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";

const recorded = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const run = recorded.find((e) => e.type === "run.started");
const graph = {
  ...demo,
  nodes: demo.nodes.map((node) => ({ ...node, examples: null })),
  positions: preset,
} as GraphSnapshot;
graph.edges = [
  ...graph.edges,
  {
    id: "reverse",
    src: "shop.pricing:compute_price",
    dst: "shop.cart:Cart.add",
    kind: "calls",
    confidence: "possible",
    reason: "unknown_receiver",
  },
];
const initial = { ...(run ? applyEvent(createInitialState(), run) : createInitialState()), graph };
stateStore.setState({ state: initial });
const host = document.createElement("div");
host.style.height = "100vh";
host.style.display = "flex";
document.body.replaceChildren(host);
function Harness() {
  const state = useStore(stateStore, (s) => s.state);
  return <GraphStage state={state} view={tabbedView} onNodeTap={() => undefined} />;
}
flushSync(() => {
  createRoot(host).render(<Harness />);
});
export const structureTest = {
  counts() {
    const moduleOf = (id: string): string => {
      const node = graph.nodes.find((n) => n.id === id);
      return node?.parent_id ? moduleOf(node.parent_id) : id;
    };
    const counts: Record<string, number> = {};
    for (const e of graph.edges)
      if (e.kind === "calls") {
        const a = moduleOf(e.src),
          b = moduleOf(e.dst);
        if (a !== b && !graph.nodes.find((n) => n.id === e.src)?.is_test)
          counts[`${a}>${b}`] = (counts[`${a}>${b}`] ?? 0) + 1;
      }
    return counts;
  },
  focus(ids: string[]) {
    const state = stateStore.getState().state;
    const status = recorded.find((event) => event.type === "agent.status");
    if (!status) throw new Error("Missing fixture agent");
    stateStore.setState({
      state: {
        ...state,
        agents: { ...state.agents, A: { ...status.payload, agent_id: "A", state: "working" } },
        focus: { A: { agent_id: "A", node_ids: ids, mode: "read", ranges: [] } },
      },
    });
  },
};
declare global {
  interface Window {
    structureTest: typeof structureTest;
  }
}
