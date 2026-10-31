import type {
  AgentStatus,
  ChangeGraph,
  ChangeProposed,
  ChangeResolved,
  ChangeUpdated,
  GraphDelta,
  GraphEdge,
  GraphNode,
  GraphSnapshot,
  RunStarted,
  ServerEvent,
} from "@contracts";
import { applyEvent, createInitialState } from "./index";

function event<K extends ServerEvent["type"]>(
  seq: number,
  type: K,
  payload: Extract<ServerEvent, { type: K }>["payload"],
): Extract<ServerEvent, { type: K }> {
  return { seq, ts: "2026-10-27T14:03:12.345Z", type, payload } as Extract<
    ServerEvent,
    { type: K }
  >;
}

const run: RunStarted = {
  run_id: "run-1",
  merge_mode: null,
  contract_version: "1.0.0",
  prompt_version: "test-prompt",
  workspace: "demo-repo",
  base_commit: "base",
  models: [{ id: "fake", label: "Fake", provider: "fake", model: "fake", reasoning: null }],
  slots: [
    { slot: "A", default_model: "fake" },
    { slot: "B", default_model: "fake" },
  ],
  limits: {
    max_steps: 10,
    max_output_tokens: 100,
    max_wall_clock_s: 60,
    test_timeout_s: 10,
    cone_max_depth: 3,
    cone_max_nodes: 20,
  },
  budget: { budget_usd: 10, spent_usd: 1, warn_at: [0.5, 0.8], block_at: 0.9 },
};
const node: GraphNode = {
  id: "shop:price",
  kind: "function",
  name: "price",
  qualname: "price",
  path: "shop.py",
  start_line: 1,
  end_line: 2,
  parent_id: "shop",
  is_test: false,
  examples: null,
};
const caller: GraphNode = { ...node, id: "shop:order", name: "order", qualname: "order" };
const edge: GraphEdge = {
  id: "calls:shop:order->shop:price",
  kind: "calls",
  src: "shop:order",
  dst: node.id,
  confidence: "exact",
  reason: null,
};
const snapshot: GraphSnapshot = {
  version: "v1",
  commit: "base",
  nodes: [node, caller],
  edges: [edge],
  parse_errors: [],
  positions: { [node.id]: { x: 10, y: 20 } },
};
const agent: AgentStatus = {
  zone: null,
  started_by: null,
  queued_task_id: null,
  base_commit: null,
  examples: null,
  answering: null,
  agent_id: "agent-1",
  change_id: "change-1",
  slot: "A",
  task: "Fix price",
  model: "fake",
  state: "working",
  step: 0,
  max_steps: 10,
  detail: null,
  plan_id: null,
};
const overlay: ChangeGraph = {
  nodes_added: [{ ...node, id: "shop:discount", name: "discount", qualname: "discount" }],
  nodes_modified: [node.id],
  nodes_removed: [],
  edges_added: [],
  edges_removed: [],
};
const update: ChangeUpdated = {
  change_id: agent.change_id,
  agent_id: agent.agent_id,
  author: "agent",
  file: {
    path: "shop.py",
    status: "modified",
    original: "def price():\n    return 0\n",
    modified: "def price():\n    return 1\n",
    lines_added: 1,
    lines_removed: 1,
  },
  node_ids: [node.id],
  graph: overlay,
  files_changed: 1,
  lines_added: 1,
  lines_removed: 1,
};
const proposal: ChangeProposed = {
  examples: null,
  change_id: agent.change_id,
  agent_id: agent.agent_id,
  revision: 1,
  task: agent.task,
  summary: "Fixed price",
  base_commit: "base",
  head_commit: "head",
  files: [{ path: "shop.py", status: "modified", lines_added: 1, lines_removed: 1 }],
  lines_added: 1,
  lines_removed: 1,
  graph: overlay,
  tests: {
    cases: null,
    cases_complete: null,
    outcome: "passed",
    passed: 2,
    failed: 0,
    skipped: 0,
    duration_ms: 3,
    failed_tests: [],
  },
  impact: {
    seeds: [node.id],
    entries: [{ node_id: "shop:order", distance: 1 }],
    edges_followed: "exact",
    max_depth: 3,
    max_nodes: 20,
    omitted_by_depth: 12,
    omitted_by_limit: 2,
    tests_reaching: 2,
  },
};
const resolution: ChangeResolved = {
  review_seq: null,
  requested_by: null,
  head_commit: null,
  change_id: agent.change_id,
  agent_id: agent.agent_id,
  revision: 1,
  decision: "approved",
  reason: null,
  comment: "Looks good",
  merge: "merged",
  merge_commit: "merged",
  conflict: null,
  decided_by: null,
  reasons: [],
  assessment: null,
  answers: null,
};
const delta: GraphDelta = {
  from_version: "v1",
  to_version: "v2",
  commit: "merged",
  cause: "merge",
  nodes_added: overlay.nodes_added,
  nodes_changed: [{ ...node, end_line: 3 }],
  nodes_removed: [],
  edges_added: [],
  edges_changed: [{ ...edge, confidence: "possible", reason: "rebound_name" }],
  edges_removed: [],
  parse_errors: [{ path: "broken.py", line: 1, message: "Invalid syntax" }],
};
const started = () => applyEvent(createInitialState(), event(1, "run.started", run));
const withGraph = () => applyEvent(started(), event(2, "graph.snapshot", snapshot));
const withAgent = () => applyEvent(withGraph(), event(3, "agent.status", agent));
const proposed = () =>
  [event(4, "change.updated", update), event(5, "change.proposed", proposal)].reduce(
    applyEvent,
    withAgent(),
  );

function freeze(value: unknown): void {
  if (value && typeof value === "object") {
    Object.freeze(value);
    for (const child of Object.values(value)) freeze(child);
  }
}

export {
  event,
  run,
  node,
  caller,
  edge,
  snapshot,
  agent,
  overlay,
  update,
  proposal,
  resolution,
  delta,
  started,
  withGraph,
  withAgent,
  proposed,
  freeze,
};
