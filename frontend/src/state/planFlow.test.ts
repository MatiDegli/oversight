import type { Plan, PlanSubtask, ServerEvent, SuiteExecution, SuiteResult } from "@contracts";
import { expect, it } from "vitest";
import { applyEvent } from "./applyEvent";
import { projectPlanFlow } from "./planFlow";
import { createInitialState, type State } from "./types";
import { agent, delta, event, proposal, resolution, run, snapshot } from "./state.support";

const task = (depends_on: number[] = [], extra: Partial<PlanSubtask> = {}): PlanSubtask => ({
  task: "Same task text",
  zone: { node_ids: [], paths: [] },
  depends_on,
  agent_id: null,
  state: "waiting",
  ...extra,
});
const plan: Plan = {
  plan_id: "p",
  revision: 1,
  task: "Synthetic dependency plan",
  graph_version: "v1",
  state: "running",
  started_by: "lead",
  queued_task_id: null,
  subtasks: [
    task([], { agent_id: agent.agent_id, state: "running" }),
    task(),
    task([0]),
    task([0, 1]),
  ],
  detail: null,
};
function append<T extends ServerEvent["type"]>(
  state: State,
  type: T,
  payload: Extract<ServerEvent, { type: T }>["payload"],
) {
  return applyEvent(state, {
    seq: state.lastSeq + 1,
    ts: "2026-11-03T19:00:00Z",
    type,
    payload,
  } as ServerEvent);
}
function base() {
  let state = append(createInitialState(), "run.started", run);
  state = append(state, "plan.updated", plan);
  return append(state, "agent.status", {
    ...agent,
    plan_id: plan.plan_id,
    base_commit: "base",
    step: 3,
  });
}
const row = (state: State, selected = plan) => projectPlanFlow(state, selected)[0];
function proposed() {
  return append(base(), "change.proposed", proposal);
}
function suite(
  state: State,
  phase: SuiteExecution["phase"],
  outcome: SuiteResult["outcome"] = "passed",
  revision = 1,
) {
  return append(state, "tests.executed", {
    change_id: proposal.change_id,
    agent_id: agent.agent_id,
    revision,
    phase,
    tested_commit: phase === "proposal" ? proposal.head_commit : "combined",
    tested_tree: "tree",
    suite: { ...proposal.tests, outcome, passed: 31, failed: outcome === "passed" ? 0 : 2 },
  });
}

it("keeps stable subtask IDs, computes waves and parallel starts from dependencies", () => {
  const state = base();
  const rows = projectPlanFlow(state, plan);
  expect(rows.map((item) => [item.index, item.wave])).toEqual([
    [0, 0],
    [1, 0],
    [2, 1],
    [3, 1],
  ]);
  expect(rows[0]).toMatchObject({
    agentId: agent.agent_id,
    word: "Working",
    progress: "Step 3 of 10",
  });
  expect(rows[2]?.condition).toBe("Starts after #1 merges");
  // Identical task text never joins an unassigned sibling to this agent/change.
  expect(rows[1]).toMatchObject({ agentId: null, changeId: null, word: "Waiting" });
  const forward: Plan = { ...plan, subtasks: [task([1]), task()] };
  expect(projectPlanFlow(state, forward).map((item) => item.wave)).toEqual([1, 0]);
});

it("keeps cancelled dependents and legacy null evidence explicit", () => {
  const cancelled: Plan = {
    ...plan,
    subtasks: [task([], { state: "rejected" }), task([0], { state: "cancelled" })],
  };
  expect(projectPlanFlow(base(), cancelled).map((item) => item.word)).toEqual([
    "Not merged",
    "Not merged",
  ]);
  expect(
    projectPlanFlow(base(), { ...plan, subtasks: [task([], { state: null })] })[0],
  ).toMatchObject({ word: "Not known", main: "not known" });
  expect(projectPlanFlow(base(), { ...plan, subtasks: null })).toEqual([]);
});

it("compares known agent base with the latest accepted main observation", () => {
  const state = proposed();
  expect(row(state)?.main).toBe("unchanged");
  expect(row(state)?.steps[3]?.mark).toBe("not needed");
  const moved = append(state, "graph.snapshot", { ...snapshot, commit: "new-main" });
  expect(row(moved)?.main).toBe("moved");
  expect(row(moved)?.steps[3]?.mark).toBe("not known");
  expect(row(append(state, "agent.status", { ...agent, base_commit: null }))?.main).toBe(
    "not known",
  );
  expect(row({ ...state, mainHeads: [] })?.steps[3]?.mark).toBe("not known");
});

it("observed combined execution overrides omission, excludes probes and stale revisions", () => {
  let state = suite(proposed(), "agent", "failed");
  state = suite(state, "head_probe", "failed");
  state = suite(state, "base_probe", "failed");
  expect(row(state)?.steps[1]?.mark).toBe("not known");
  state = suite(state, "proposal");
  expect(row(state)?.steps[1]).toMatchObject({
    mark: "passed",
    detail: "passed: 31 passed, 0 failed",
  });
  state = suite(state, "combined", "failed", 2);
  expect(row(state)?.steps[3]?.mark).toBe("not needed");
  state = suite(state, "combined");
  expect(row(state)?.steps[3]?.mark).toBe("passed");
  const replaced = append(state, "change.proposed", {
    ...proposal,
    revision: 2,
    head_commit: "new-head",
  });
  expect(row(replaced)?.steps[1]?.mark).toBe("not known");
  expect(row(replaced)?.steps[3]?.mark).toBe("failed");
});

it("does not freeze current-revision main evidence at a delayed prior-revision conflict", () => {
  const conflict = {
    ...resolution,
    merge: "conflict" as const,
    merge_commit: null,
    conflict: { kind: "merge" as const, paths: [], tests: null },
  };
  let state = append(proposed(), "change.resolved", conflict);
  state = append(state, "change.proposed", { ...proposal, revision: 2, head_commit: "head-2" });
  expect(row(state)?.steps[3]?.mark).toBe("not needed");
  // A delayed old resolution is retained by the production fold after the replacement proposal.
  state = append(state, "change.resolved", conflict);
  state = append(state, "graph.snapshot", { ...snapshot, commit: "moved-for-revision-2" });
  expect(row(state)).toMatchObject({ main: "moved", merged: false, word: "Waiting for you" });
  expect(row(state)?.steps[3]).toEqual({
    mark: "not known",
    detail: "Main moved; no combined execution recorded",
  });
  expect(row(state)?.steps[3]?.mark).not.toBe("not needed");
});

it.each(["failed", "error", "timeout"] as const)(
  "shows combined %s counts without manufacturing a merge",
  (outcome) => {
    let state = suite(proposed(), "combined", outcome);
    state = append(state, "change.resolved", {
      ...resolution,
      merge: "conflict",
      merge_commit: null,
      decided_by: "person",
      conflict: {
        kind: "tests",
        paths: [],
        tests: { ...proposal.tests, outcome, passed: 31, failed: 2 },
      },
    });
    expect(row(state)).toMatchObject({ word: "Not merged", merged: false });
    expect(row(state)?.steps[2]).toMatchObject({ mark: "passed", detail: "approved · person" });
    expect(row(state)?.steps[3]).toMatchObject({
      mark: "failed",
      detail: `${outcome}: 31 passed, 2 failed`,
    });
  },
);

it("approval and a merged subtask word alone never prove a successful merge", () => {
  const state = append(proposed(), "change.resolved", {
    ...resolution,
    merge: "conflict",
    merge_commit: null,
  });
  expect(
    row(state, { ...plan, subtasks: [task([], { agent_id: agent.agent_id, state: "merged" })] })
      ?.merged,
  ).toBe(false);
  const merged = append(proposed(), "change.resolved", resolution);
  expect(row(merged)).toMatchObject({ word: "Merged", merged: true });
});

it("retains terminal gate inference through its own merge, delayed graph evidence and later merges", () => {
  const state = proposed();
  const beforeResolution = append(state, "graph.snapshot", {
    ...snapshot,
    commit: resolution.merge_commit ?? "",
  });
  const merged = append(beforeResolution, "change.resolved", resolution);
  expect(row(merged)?.steps[3]?.mark).toBe("not needed");
  const later = append(merged, "graph.delta", {
    ...delta,
    from_version: "v1",
    commit: "another-merge",
  });
  expect(row(later)?.main).toBe("unchanged");
  expect(row(later)?.steps[3]?.mark).toBe("not needed");
  const moved = append(state, "graph.snapshot", { ...snapshot, commit: "other-before" });
  expect(row(append(moved, "change.resolved", resolution))?.main).toBe("moved");
});

it("tracks resolution and revert heads in event order, ignoring rejected and gapped folds", () => {
  const merged = append(proposed(), "change.resolved", resolution);
  expect(merged.mainHeads.at(-1)?.commit).toBe("merged");
  const reverted = append(merged, "revert.resolved", {
    change_id: proposal.change_id,
    revision: 1,
    resolution_seq: merged.lastSeq,
    merge_commit: "merged",
    decided_by: "person",
    result: "reverted",
    main_commit: "reverted",
    revert_commit: "reverted",
    paths: [],
    reason: null,
    tests: proposal.tests,
  });
  expect(reverted.mainHeads.at(-1)?.commit).toBe("reverted");
  const rejected = applyEvent(reverted, event(99, "graph.snapshot", snapshot));
  expect(rejected.mainHeads).toEqual(reverted.mainHeads);
  const gap = append(reverted, "graph.delta", { ...delta, from_version: "missing" });
  expect(gap.gap).toBe(true);
  expect(gap.mainHeads).toEqual(reverted.mainHeads);
  const reset = applyEvent(
    reverted,
    event(1, "run.started", { ...run, run_id: "another", base_commit: "fresh" }),
  );
  expect(reset.mainHeads).toEqual([{ seq: 1, commit: "fresh" }]);
  expect(projectPlanFlow(reset, plan)[0]?.main).toBe("not known");
});

it("folds each live/replay prefix identically without mutating evidence", () => {
  const live = suite(append(proposed(), "graph.snapshot", snapshot), "combined");
  // Reconstruct the same accepted envelopes; graph and test events are not decision envelopes.
  const events: ServerEvent[] = [
    event(1, "run.started", run),
    event(2, "plan.updated", plan),
    event(3, "agent.status", { ...agent, plan_id: "p", base_commit: "base", step: 3 }),
    event(4, "change.proposed", proposal),
    event(5, "graph.snapshot", snapshot),
    event(
      6,
      "tests.executed",
      Object.values(live.testExecutions)[0] ??
        (() => {
          throw new Error("Missing execution");
        })(),
    ),
  ];
  let streamed = createInitialState();
  for (let i = 0; i < events.length; i++) {
    const next = events[i];
    if (!next) throw new Error("Missing event");
    streamed = applyEvent(streamed, next);
    const replay = events.slice(0, i + 1).reduce(applyEvent, createInitialState());
    const before = JSON.stringify(replay);
    expect(projectPlanFlow(streamed, plan)).toEqual(projectPlanFlow(replay, plan));
    expect(JSON.stringify(replay)).toBe(before);
  }
  expect(projectPlanFlow(streamed, plan)).toEqual(projectPlanFlow(live, plan));
});
