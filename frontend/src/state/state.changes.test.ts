import type { ServerEvent } from "@contracts";
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
import {
  event,
  run,
  node,
  snapshot,
  agent,
  overlay,
  update,
  proposal,
  resolution,
  delta,
  withAgent,
  proposed,
  freeze,
} from "./state.support";

describe("applyEvent", () => {
  it("replays a paused status and restores working without changing the graph or focus", () => {
    const before = applyEvent(
      withAgent(),
      event(4, "agent.focus", {
        agent_id: agent.agent_id,
        mode: "read",
        node_ids: [node.id],
        ranges: [],
      }),
    );
    const paused = applyEvent(before, event(5, "agent.status", { ...agent, state: "paused" }));
    expect(paused.agents[agent.agent_id]?.state).toBe("paused");
    expect(selectHasReadyForReview(paused)).toBe(false);
    const resumed = applyEvent(paused, event(6, "agent.status", agent));
    expect(resumed.agents[agent.agent_id]).toEqual(agent);
    expect(resumed.graph).toBe(before.graph);
    expect(resumed.focus).toEqual(before.focus);
    expect(resumed.steps).toEqual(before.steps);
    expect(resumed.gap).toBe(false);
    const store = createStateStore(before);
    store
      .getState()
      .applyEvents([
        event(5, "agent.status", { ...agent, state: "paused" }),
        event(6, "agent.status", agent),
      ]);
    expect(store.getState().state).toEqual(resumed);
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
      error_kind: null,
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
        error_kind: null,
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
      role: null,
      change_id: null,
      plan_id: null,
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
      error_kind: null,
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
