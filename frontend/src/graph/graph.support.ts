import type { AgentStatus, GraphNode, ServerEvent } from "@contracts";
import type { Core } from "cytoscape";
import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import { afterEach } from "vitest";

import type { State } from "../state";
import { applyEvent, createInitialState, stateStore } from "../state";
import { themes } from "../theme";

import { graphElements } from "./elements";
import { graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

cytoscape.use(fcose);

const theme = themes.dark;
const agents = { A: "agent-a", B: "agent-b" } as const;

const rgb = (hex: string) =>
  `rgb(${[1, 3, 5].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16)).join(",")})`;

const node = (id: string, kind: GraphNode["kind"], parent_id: string | null = null): GraphNode => ({
  id,
  kind,
  parent_id,
  name: id,
  qualname: id,
  path: "billing.py",
  start_line: 1,
  end_line: 4,
  is_test: false,
  examples: null,
});
const nodes = [
  node("billing", "module"),
  node("billing:Bill", "class", "billing"),
  node("billing:Bill.total", "method", "billing:Bill"),
  node("billing:fee", "function", "billing"),
];
const agent = (slot: "A" | "B"): AgentStatus => ({
  zone: null,
  started_by: null,
  queued_task_id: null,
  base_commit: null,
  examples: null,
  answering: null,
  agent_id: `agent-${slot}`,
  change_id: `change-${slot}`,
  slot,
  task: "Update billing",
  model: "fake",
  state: "working",
  step: 1,
  max_steps: 10,
  detail: null,
  plan_id: null,
});
const a = agent("A");
const b = agent("B");
function event<K extends ServerEvent["type"]>(
  seq: number,
  type: K,
  payload: Extract<ServerEvent, { type: K }>["payload"],
): ServerEvent {
  return { seq, ts: "2026-10-26T15:00:00.000Z", type, payload } as ServerEvent;
}
const log: ServerEvent[] = [
  event(1, "run.started", {
    run_id: "run-graph-test",
    merge_mode: null,
    contract_version: "1.0.0",
    prompt_version: "test",
    workspace: "demo-repo",
    base_commit: "base",
    models: [],
    slots: [],
    limits: {
      max_steps: 10,
      max_output_tokens: 100,
      max_wall_clock_s: 60,
      test_timeout_s: 10,
      cone_max_depth: 3,
      cone_max_nodes: 20,
    },
    budget: { budget_usd: 10, spent_usd: 0, warn_at: [0.5, 0.8], block_at: 0.9 },
  }),
  event(2, "graph.snapshot", {
    version: "v1",
    commit: "base",
    nodes,
    edges: ["calls", "imports", "inherits"].map((kind, i) => ({
      id: `edge-${kind}`,
      kind: kind as "calls" | "imports" | "inherits",
      src: "billing:Bill.total",
      dst: "billing:fee",
      confidence: i === 0 ? "possible" : "exact",
      reason: i === 0 ? "unknown_receiver" : null,
    })),
    positions: { "billing:Bill.total": { x: 100, y: 100 }, "billing:fee": { x: 400, y: 100 } },
    parse_errors: [],
  }),
  event(3, "agent.status", a),
  event(4, "agent.status", b),
];
const initial = () => log.reduce(applyEvent, createInitialState());
function updated(state = initial()): State {
  return applyEvent(
    state,
    event(state.lastSeq + 1, "change.updated", {
      change_id: a.change_id,
      agent_id: a.agent_id,
      author: "agent",
      file: {
        path: "billing.py",
        status: "modified",
        original: "before",
        modified: "after",
        lines_added: 1,
        lines_removed: 1,
      },
      node_ids: ["billing:fee"],
      files_changed: 1,
      lines_added: 1,
      lines_removed: 1,
      graph: {
        nodes_added: [node("billing:discount", "function", "billing")],
        nodes_modified: ["billing:fee"],
        nodes_removed: ["billing:Bill.total"],
        edges_added: [
          {
            id: "added-call",
            kind: "calls",
            src: "billing:discount",
            dst: "billing:fee",
            confidence: "exact",
            reason: null,
          },
        ],
        edges_removed: ["edge-imports"],
      },
    }),
  );
}
let cy: Core | undefined;
function render(state: State) {
  cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(theme) });
  updateGraph(cy, graphElements(state));
  return cy;
}
afterEach(() => {
  cy?.destroy();
  stateStore.setState({ state: createInitialState() });
});

export { a, agents, b, event, initial, log, node, nodes, render, rgb, theme, updated };
