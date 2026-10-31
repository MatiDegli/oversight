import { describe, expect, it } from "vitest";
import {
  applyEvent,
  createInitialState,
  createStateStore,
  selectAgents,
  selectAgentCard,
  selectNodeMarks,
  selectSpend,
} from "./index";
import {
  event,
  run,
  node,
  caller,
  edge,
  snapshot,
  agent,
  overlay,
  update,
  delta,
  started,
  withGraph,
  withAgent,
  proposed,
  freeze,
} from "./state.support";

describe("applyEvent", () => {
  it("keeps two interleaved agents' cards, steps and graph marks separate (K14)", () => {
    const b = {
      ...agent,
      agent_id: "agent-2",
      change_id: "change-2",
      slot: "B" as const,
      task: "Fix order",
    };
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
    const log = [
      event(1, "run.started", run),
      event(2, "graph.snapshot", snapshot),
      event(3, "agent.status", b),
      event(4, "agent.status", agent),
      event(5, "agent.step", step),
      event(6, "agent.focus", {
        agent_id: b.agent_id,
        mode: "edit",
        node_ids: [caller.id],
        ranges: [],
      }),
      event(7, "agent.step", { ...step, agent_id: b.agent_id, summary: "Edit order" }),
      event(8, "change.updated", update),
      event(9, "change.updated", {
        ...update,
        agent_id: b.agent_id,
        change_id: b.change_id,
        graph: { ...overlay, nodes_added: [], nodes_modified: [caller.id] },
        node_ids: [caller.id],
      }),
      event(10, "agent.focus", {
        agent_id: agent.agent_id,
        mode: "read",
        node_ids: [node.id],
        ranges: [],
      }),
    ];
    const state = log.reduce(applyEvent, createInitialState());
    expect(selectAgents(state).map((entry) => entry.slot)).toEqual(["A", "B"]);
    expect(selectAgentCard(state, agent.agent_id)?.steps).toEqual([step]);
    expect(selectAgentCard(state, b.agent_id)?.steps[0]?.summary).toBe("Edit order");
    expect(selectAgentCard(state, b.agent_id)?.agent.task).toBe("Fix order");
    expect(selectNodeMarks(state)[node.id]).toEqual({
      pending: true,
      pendingSlots: ["A"],
      focus: { A: "read" },
    });
    expect(selectNodeMarks(state)[caller.id]).toEqual({
      pending: true,
      pendingSlots: ["B"],
      focus: { B: "edit" },
    });
  });

  it.each(["baseten", "fake"] as const)(
    "replays the same spend knowledge and latest %s ledger as live batches (K18)",
    (provider) => {
      const profile = {
        id: "meta",
        label: "Meta",
        provider: "meta" as const,
        model: "meta-api",
        reasoning: null,
      };
      const usage = {
        role: null,
        change_id: null,
        plan_id: null,
        agent_id: agent.agent_id,
        model: "meta-api",
        input_tokens: null,
        output_tokens: null,
        cached_tokens: null,
        cost_usd: 0.02,
        spent_usd: 3,
        budget_usd: 10,
        brake: "ok" as const,
      };
      const b = {
        ...agent,
        agent_id: "agent-2",
        change_id: "change-2",
        slot: "B" as const,
        model: "other",
      };
      const log = [
        event(1, "run.started", {
          ...run,
          models: [profile, { ...profile, id: "other", provider }],
        }),
        event(2, "agent.status", { ...agent, model: "meta" }),
        event(3, "agent.status", b),
        event(4, "usage.tick", usage),
        event(5, "usage.tick", { ...usage, agent_id: b.agent_id, cost_usd: null }),
        event(6, "usage.tick", { ...usage, cost_usd: null }),
        event(7, "usage.tick", {
          ...usage,
          agent_id: b.agent_id,
          cost_usd: null,
          spent_usd: 3.5,
          brake: "blocked",
        }),
      ];
      const store = createStateStore();
      let replay = createInitialState();
      for (const entry of log) {
        replay = applyEvent(replay, entry);
        store.getState().applyEvents([entry]);
        expect(store.getState().state).toEqual(replay);
        if (entry.seq === 5) expect(selectSpend(replay)?.spentUsd).toBe(3);
      }
      expect(replay.spendUnknown).toBe(true);
      expect(replay.usage?.spent_usd).toBe(3.5);
      expect(selectSpend(replay)).toEqual({
        spentUsd: null,
        budgetUsd: 10,
        brake: "blocked",
      });
    },
  );
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
        error_kind: null,
        duration_ms: 1,
        tests: null,
      }),
      event(8, "usage.tick", {
        role: null,
        change_id: null,
        plan_id: null,
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
      runStartedAt: "2026-10-27T14:03:12.345Z",
      lastSeq: 1,
      // The new run's first event is its "now"; nothing of the old run's times survives.
      clock: "2026-10-27T14:03:12.345Z",
    });
    expect(applyEvent(dirty, event(2, "run.started", newRun))).toEqual({ ...dirty, gap: true });
    expect(applyEvent(dirty, event(1, "run.started", run))).toEqual({ ...dirty, gap: true });
  });

  it("keeps each step's event time by agent and step, and the latest event's time as the clock", () => {
    const step = (seq: number, n: number, ts: string) => ({
      ...event(seq, "agent.step", {
        agent_id: agent.agent_id,
        step: n,
        tool: "read_file",
        summary: "Read",
        result: "ok",
        error: null,
        error_kind: null,
        duration_ms: 1,
        tests: null,
      }),
      ts,
    });
    const first = applyEvent(proposed(), step(6, 1, "2026-10-27T14:05:00.000Z"));
    const second = applyEvent(first, step(7, 2, "2026-10-27T14:05:40.000Z"));
    expect(second.stepTimes[agent.agent_id]).toEqual({
      1: "2026-10-27T14:05:00.000Z",
      2: "2026-10-27T14:05:40.000Z",
    });
    expect(second.clock).toBe("2026-10-27T14:05:40.000Z");
    // Any event moves the clock, and the earlier maps are left as they were.
    const later = applyEvent(second, {
      ...event(8, "error", {
        source: "server",
        message: "Example",
        agent_id: null,
        change_id: null,
        fatal: false,
      }),
      ts: "2026-10-27T14:06:00.000Z",
    });
    expect(later.clock).toBe("2026-10-27T14:06:00.000Z");
    expect(first.stepTimes[agent.agent_id]).toEqual({ 1: "2026-10-27T14:05:00.000Z" });
    // An event out of sequence changes neither.
    const gap = applyEvent(later, { ...step(20, 3, "2026-10-27T14:07:00.000Z") });
    expect(gap.gap).toBe(true);
    expect(gap.clock).toBe(later.clock);
    expect(gap.stepTimes).toBe(later.stepTimes);
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
});
