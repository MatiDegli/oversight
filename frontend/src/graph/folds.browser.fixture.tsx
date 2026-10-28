// Browser harness: no application transport, backend, or model calls.
import type { GraphNode, GraphSnapshot } from "@contracts";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import { createChange, createInitialState, stateStore } from "../state";

import { GraphView } from "./GraphView";

const node = (id: string, kind: GraphNode["kind"], parent_id: string | null): GraphNode => ({
  id,
  kind,
  parent_id,
  name: id.split(":").pop() ?? id,
  qualname: id,
  path: "shop.py",
  start_line: 1,
  end_line: 2,
  is_test: false,
  examples: null,
});
const graph: GraphSnapshot = {
  version: "v1",
  commit: "base",
  parse_errors: [],
  nodes: [
    node("cart", "module", null),
    node("Cart", "class", "cart"),
    node("Cart:add", "method", "Cart"),
    node("Cart:total", "method", "Cart"),
    node("after", "function", "cart"),
    node("Tail", "class", "cart"),
    node("inventory", "module", null),
    node("stock", "function", "inventory"),
    node("pricing", "module", null),
    node("compute", "function", "pricing"),
  ],
  edges: ["Cart:total", "after", "stock"].map((src) => ({
    id: `${src}>compute`,
    src,
    dst: "compute",
    kind: "calls",
    confidence: "exact",
    reason: null,
  })),
  positions: {
    cart: { x: 170, y: 200 },
    Cart: { x: 170, y: 240 },
    "Cart:add": { x: 170, y: 220 },
    "Cart:total": { x: 170, y: 277 },
    after: { x: 170, y: 370 },
    Tail: { x: 170, y: 460 },
    inventory: { x: 170, y: 550 },
    stock: { x: 170, y: 700 },
    pricing: { x: 650, y: 200 },
    compute: { x: 650, y: 200 },
  },
};
stateStore.setState({
  state: {
    ...createInitialState(),
    graph,
    focus: {
      A: { agent_id: "A", mode: "read", node_ids: ["Cart:add", "stock", "compute"], ranges: [] },
    },
  },
});
const host = document.createElement("div");
host.style.height = "100vh";
document.body.replaceChildren(host);
flushSync(() => {
  createRoot(host).render(<GraphView />);
});

export const foldTest = {
  edit() {
    const state = stateStore.getState().state;
    stateStore.setState({
      state: {
        ...state,
        changes: {
          change: {
            ...createChange("change", "A"),
            change_id: "change",
            agent_id: "A",
            status: "editing",
            files: {},
            graph: {
              nodes_added: [],
              nodes_modified: ["Cart:add"],
              nodes_removed: [],
              edges_added: [],
              edges_removed: [],
            },
            files_changed: 1,
            lines_added: 1,
            lines_removed: 0,
            proposal: null,
            resolution: null,
          },
        },
      },
    });
  },
  tick() {
    stateStore.setState({ state: { ...stateStore.getState().state, lastSeq: 99 } });
  },
};
