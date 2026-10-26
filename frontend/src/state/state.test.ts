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
import { describe, expect, it, vi } from "vitest";

import {
  applyEvent,
  createInitialState,
  createStateStore,
  selectAgents,
  selectChangeForAgent,
  selectHasReadyForReview,
  selectLitNodes,
  selectPendingChanges,
} from "./index";

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
  agent_id: "agent-1",
  change_id: "change-1",
  slot: "A",
  task: "Fix price",
  model: "fake",
  state: "working",
  step: 0,
  max_steps: 10,
  detail: null,
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
  tests: { outcome: "passed", passed: 2, failed: 0, skipped: 0, duration_ms: 3, failed_tests: [] },
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
  change_id: agent.change_id,
  agent_id: agent.agent_id,
  revision: 1,
  decision: "approved",
  reason: null,
  comment: "Looks good",
  merge: "merged",
  merge_commit: "merged",
  conflict: null,
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

describe("applyEvent", () => {
  it("starts with fresh plain data and sets the main snapshot", () => {
    const state = withGraph();
    expect(state.run).toEqual(run);
    expect(state.lastSeq).toBe(2);
    expect(state.graph).toEqual(snapshot);
    expect(createInitialState()).not.toBe(createInitialState());
    expect(JSON.parse(JSON.stringify(state))).toEqual(state);
  });

  it("rejects gaps, duplicates and out-of-order events without advancing, then resumes", () => {
    const before = withAgent();
    for (const seq of [2, 3, 5]) {
      const gap = applyEvent(
        before,
        event(seq, "graph.snapshot", { ...snapshot, version: "wrong" }),
      );
      expect(gap).toEqual({ ...before, gap: true });
      expect(applyEvent(gap, event(4, "graph.snapshot", snapshot)).gap).toBe(false);
    }
    expect(applyEvent(createInitialState(), event(1, "graph.snapshot", snapshot))).toEqual({
      ...createInitialState(),
      gap: true,
    });
  });

  it("resets every field for another run at seq 1, even after a gap", () => {
    const dirty = [
      event(6, "agent.focus", {
        agent_id: agent.agent_id,
        mode: "edit",
        node_ids: [node.id],
        ranges: [],
      }),
      event(7, "agent.step", {
        agent_id: agent.agent_id,
        step: 1,
        tool: "finish",
        summary: "Done",
        result: "ok",
        error: null,
        duration_ms: 1,
        tests: null,
      }),
      event(8, "usage.tick", {
        agent_id: agent.agent_id,
        model: null,
        input_tokens: null,
        output_tokens: null,
        cached_tokens: null,
        cost_usd: null,
        spent_usd: 2,
        budget_usd: 10,
        brake: "ok",
      }),
      event(9, "error", {
        source: "server",
        message: "Example failure",
        agent_id: null,
        change_id: null,
        fatal: false,
      }),
    ].reduce(applyEvent, proposed());
    const newRun = { ...run, run_id: "run-2" };
    expect(applyEvent({ ...dirty, gap: true }, event(1, "run.started", newRun))).toEqual({
      ...createInitialState(),
      run: newRun,
      lastSeq: 1,
    });
    expect(applyEvent(dirty, event(2, "run.started", newRun))).toEqual({ ...dirty, gap: true });
    expect(applyEvent(dirty, event(1, "run.started", run))).toEqual({ ...dirty, gap: true });
  });

  it("patches main by id, retains positions, and keeps another change's overlay intact", () => {
    const before = proposed();
    freeze(before);
    const incoming = event(6, "graph.delta", delta);
    freeze(incoming);
    const state = applyEvent(before, incoming);
    expect(state.graph).toEqual({
      ...snapshot,
      version: "v2",
      commit: "merged",
      nodes: [delta.nodes_changed[0], caller, ...overlay.nodes_added],
      edges: delta.edges_changed,
      parse_errors: delta.parse_errors,
    });
    expect(state.changes).toBe(before.changes);
    expect(state.graph?.positions).toBe(before.graph?.positions);
    const removed = applyEvent(
      state,
      event(7, "graph.delta", {
        ...delta,
        from_version: "v2",
        to_version: "v3",
        nodes_added: [],
        nodes_changed: [],
        nodes_removed: [node.id],
        edges_added: [{ ...edge, id: "new-edge" }],
        edges_changed: [],
        edges_removed: [edge.id],
        parse_errors: [],
      }),
    );
    expect(removed.graph?.nodes).toEqual([caller, ...overlay.nodes_added]);
    expect(removed.graph?.edges).toEqual([]);
    expect(removed.graph?.parse_errors).toEqual([]);
  });

  it("prunes incoming and outgoing edges when a delta removes only a node", () => {
    const outgoing = { ...edge, id: "outgoing", src: node.id, dst: caller.id };
    const retained = { ...edge, id: "retained", src: caller.id, dst: caller.id };
    const before = applyEvent(
      started(),
      event(2, "graph.snapshot", { ...snapshot, edges: [edge, outgoing, retained] }),
    );
    const incoming = event(3, "graph.delta", {
      ...delta,
      nodes_added: [],
      nodes_changed: [],
      nodes_removed: [node.id],
      edges_added: [],
      edges_changed: [],
      edges_removed: [],
      parse_errors: [],
    });
    freeze(before);
    freeze(incoming);
    const state = applyEvent(before, incoming);
    expect(state.graph?.nodes).toEqual([caller]);
    expect(state.graph?.edges).toEqual([retained]);
    expect(before.graph?.edges).toEqual([edge, outgoing, retained]);
  });

  it("does not consume a graph delta against a missing or wrong version", () => {
    for (const before of [started(), withGraph()]) {
      const state = applyEvent(
        before,
        event(before.lastSeq + 1, "graph.delta", {
          ...delta,
          from_version: "wrong",
        }),
      );
      expect(state).toEqual({ ...before, gap: true });
      expect(state.graph).toBe(before.graph);
    }
  });

  it("keeps agent statuses, card steps and read/edit focus independent", () => {
    const second = { ...agent, agent_id: "agent-2", change_id: "change-2", slot: "B" as const };
    const focus = {
      agent_id: agent.agent_id,
      mode: "read" as const,
      node_ids: [node.id],
      ranges: [{ path: "shop.py", start_line: 1, end_line: 2 }],
    };
    const step = {
      agent_id: agent.agent_id,
      step: 1,
      tool: "read_file",
      summary: "Read price",
      result: "ok" as const,
      error: null,
      duration_ms: 2,
      tests: null,
    };
    const state = [
      event(4, "agent.status", second),
      event(5, "agent.focus", focus),
      event(6, "agent.step", step),
      event(7, "agent.focus", { ...focus, agent_id: second.agent_id, mode: "edit" }),
      event(8, "agent.focus", { ...focus, mode: "edit", node_ids: ["shop:discount"] }),
      event(9, "agent.step", {
        ...step,
        step: 2,
        tool: "run_tests",
        result: "error",
        error: "Tests failed",
        tests: { ...proposal.tests, outcome: "failed", failed: 1, failed_tests: ["test_price"] },
      }),
      event(10, "agent.status", { ...agent, state: "halted", step: 2, detail: "Step limit" }),
    ].reduce(applyEvent, withAgent());
    expect(selectAgents(state).map((value) => value.slot)).toEqual(["A", "B"]);
    expect(selectAgents({ ...state, agents: { b: second, a: agent } })[0]).toEqual(agent);
    expect(state.agents[agent.agent_id]?.detail).toBe("Step limit");
    expect(state.steps[agent.agent_id]).toEqual([
      step,
      expect.objectContaining({ step: 2, result: "error" }),
    ]);
    expect(selectLitNodes(state)[agent.agent_id]).toEqual({
      ...focus,
      mode: "edit",
      node_ids: ["shop:discount"],
    });
    expect(selectLitNodes(state)[second.agent_id]).toEqual({
      ...focus,
      agent_id: second.agent_id,
      mode: "edit",
    });
    expect(selectChangeForAgent(state, agent.agent_id)?.status).toBe("editing");
    expect(selectChangeForAgent(state, "unknown")).toBeUndefined();
  });

  it("replaces whole overlays and per-file diffs, retaining other files and authors", () => {
    const newFile = { ...update.file, path: "added.py", status: "added" as const, original: null };
    const latestGraph = { ...overlay, nodes_removed: [node.id] };
    const state = [
      event(4, "change.updated", update),
      event(5, "change.updated", { ...update, file: newFile, files_changed: 2 }),
      event(6, "change.updated", {
        ...update,
        author: "user",
        graph: latestGraph,
        file: { ...update.file, status: "deleted", modified: null },
        files_changed: 2,
        lines_removed: 4,
      }),
    ].reduce(applyEvent, withAgent());
    const change = selectChangeForAgent(state, agent.agent_id);
    expect(change?.files["shop.py"]).toEqual({
      file: { ...update.file, status: "deleted", modified: null },
      author: "user",
      node_ids: update.node_ids,
    });
    expect(change?.files["added.py"]?.file).toEqual(newFile);
    expect(change?.graph).toEqual(latestGraph);
    expect(change?.files_changed).toBe(2);
    expect(change?.lines_removed).toBe(4);
    expect(state.graph).toEqual(snapshot);
  });

  it("proposes tests and bounded impact without altering main or agent status", () => {
    const state = proposed();
    expect(selectChangeForAgent(state, agent.agent_id)?.proposal).toEqual(proposal);
    expect(selectHasReadyForReview(state)).toBe(true);
    expect(state.agents[agent.agent_id]).toEqual(agent);
    expect(state.graph).toEqual(snapshot);
  });

  it("retains files on conflicts and revisions, then replaces review evidence on reproposal", () => {
    const before = proposed();
    const conflict = applyEvent(
      before,
      event(6, "change.resolved", {
        ...resolution,
        merge: "conflict",
        merge_commit: null,
        conflict: {
          kind: "tests",
          paths: [],
          tests: { ...proposal.tests, outcome: "failed", failed: 1 },
        },
      }),
    );
    expect(selectChangeForAgent(conflict, agent.agent_id)?.status).toBe("conflict");
    expect(selectHasReadyForReview(conflict)).toBe(true);
    expect(selectPendingChanges(conflict)).toHaveLength(1);
    const revising = applyEvent(
      conflict,
      event(7, "change.resolved", {
        ...resolution,
        decision: "changes_requested",
        merge: null,
        merge_commit: null,
        comment: "Fix tests",
      }),
    );
    expect(selectChangeForAgent(revising, agent.agent_id)?.status).toBe("revising");
    expect(selectChangeForAgent(revising, agent.agent_id)?.files).toBe(
      before.changes[agent.change_id]?.files,
    );
    expect(selectHasReadyForReview(revising)).toBe(false);
    const again = applyEvent(
      revising,
      event(8, "change.proposed", { ...proposal, revision: 2, head_commit: "revision-2" }),
    );
    expect(selectChangeForAgent(again, agent.agent_id)?.resolution).toBeNull();
    expect(selectChangeForAgent(again, agent.agent_id)?.proposal?.revision).toBe(2);
    expect(selectHasReadyForReview(again)).toBe(true);
  });

  it("closes a stopped change only on change.resolved, including before its first edit", () => {
    const stopped = applyEvent(
      withAgent(),
      event(4, "agent.status", { ...agent, state: "stopped" }),
    );
    expect(selectPendingChanges(stopped)).toHaveLength(1);
    const closed = applyEvent(
      stopped,
      event(5, "change.resolved", {
        ...resolution,
        revision: 0,
        decision: "rejected",
        reason: "stopped",
        merge: null,
        merge_commit: null,
      }),
    );
    expect(selectPendingChanges(closed)).toEqual([]);
    expect(selectChangeForAgent(closed, agent.agent_id)?.resolution?.reason).toBe("stopped");
  });

  it("preserves unknown usage as null and records errors without inventing statuses", () => {
    const usage = {
      agent_id: agent.agent_id,
      model: null,
      input_tokens: null,
      output_tokens: 0,
      cached_tokens: null,
      cost_usd: null,
      spent_usd: 8,
      budget_usd: 10,
      brake: "warn_80" as const,
    };
    const error = {
      source: "tools" as const,
      message: "Tool failed",
      agent_id: agent.agent_id,
      change_id: agent.change_id,
      fatal: true,
    };
    const state = [
      event(4, "usage.tick", usage),
      event(5, "error", error),
      event(6, "error", { ...error, fatal: false }),
    ].reduce(applyEvent, withAgent());
    expect(state.usage).toEqual(usage);
    expect(state.errors).toEqual([error, { ...error, fatal: false }]);
    expect(state.agents).toEqual(withAgent().agents);
  });

  it("plays the level 1 story from start through read, edit, propose, merge and done", () => {
    const focus = { agent_id: agent.agent_id, node_ids: [node.id], ranges: [] };
    const step = {
      agent_id: agent.agent_id,
      step: 1,
      tool: "read_file",
      summary: "Read price",
      result: "ok" as const,
      error: null,
      duration_ms: 1,
      tests: null,
    };
    const log: ServerEvent[] = [
      event(1, "run.started", run),
      event(2, "graph.snapshot", snapshot),
      event(3, "agent.status", agent),
      event(4, "agent.focus", { ...focus, mode: "read" }),
      event(5, "agent.step", step),
      event(6, "change.updated", update),
      event(7, "agent.focus", { ...focus, mode: "edit" }),
      event(8, "agent.step", { ...step, step: 2, tool: "edit_file", summary: "Fixed price" }),
      event(9, "change.proposed", proposal),
      event(10, "agent.status", { ...agent, state: "ready_for_review", step: 2 }),
      event(11, "change.resolved", resolution),
      event(12, "graph.delta", delta),
      event(13, "agent.status", { ...agent, state: "done", step: 2 }),
    ];
    freeze(log);
    const store = createStateStore();
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    store.getState().applyEvents(log);
    unsubscribe();
    const state = store.getState().state;
    expect(listener).toHaveBeenCalledTimes(1);
    expect(state).toEqual(log.reduce(applyEvent, createInitialState()));
    expect(state.lastSeq).toBe(13);
    expect(state.gap).toBe(false);
    expect(state.graph?.version).toBe("v2");
    expect(state.graph?.commit).toBe("merged");
    expect(state.agents[agent.agent_id]?.state).toBe("done");
    expect(state.steps[agent.agent_id]).toHaveLength(2);
    expect(selectChangeForAgent(state, agent.agent_id)?.resolution).toEqual(resolution);
    expect(selectChangeForAgent(state, agent.agent_id)?.files["shop.py"]?.file).toEqual(
      update.file,
    );
    expect(selectHasReadyForReview(state)).toBe(false);
    expect(selectPendingChanges(state)).toEqual([]);
    expect(createStateStore().getState().state.lastSeq).toBe(0);
  });

  it("does not advance a batch past a missing event and accepts a resumed batch", () => {
    const store = createStateStore(withAgent());
    store
      .getState()
      .applyEvents([
        event(5, "change.proposed", proposal),
        event(6, "change.resolved", resolution),
      ]);
    expect(store.getState().state).toEqual({ ...withAgent(), gap: true });
    store
      .getState()
      .applyEvents([event(4, "change.updated", update), event(5, "change.proposed", proposal)]);
    expect(store.getState().state).toEqual(proposed());
  });
});
