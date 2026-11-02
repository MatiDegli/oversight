import type { AgentStatus, ServerEvent } from "@contracts";
import { expect, it } from "vitest";
import recorded from "../../../fixtures/events/recorded-demo.jsonl?raw";
import { parseEvent } from "../transport";
import { applyEvent } from "./applyEvent";
import { selectTeams } from "./teamSelector";
import { createInitialState } from "./types";

const ts = "2026-11-02T15:00:00.000Z";
const opening = parseEvent(recorded.trim().split("\n")[0] ?? "");
if (opening.type !== "run.started") throw new Error("Missing run fixture");

function status(
  id: string,
  plan: string | null = null,
  extra: Partial<AgentStatus> = {},
): ServerEvent {
  return {
    seq: 1,
    ts,
    type: "agent.status",
    payload: {
      agent_id: id,
      change_id: `change-${id.slice(6)}`,
      slot: "A",
      task: "Implement the task",
      model: "fake",
      state: "working",
      step: 0,
      max_steps: 10,
      detail: null,
      plan_id: plan,
      zone: null,
      started_by: null,
      queued_task_id: null,
      base_commit: null,
      examples: null,
      answering: null,
      ...extra,
    },
  };
}

function plan(id: string, lead: string | null = null, revision = 1): ServerEvent {
  return {
    seq: 1,
    ts,
    type: "plan.updated",
    payload: {
      plan_id: id,
      started_by: lead,
      revision,
      task: "Plan the task",
      graph_version: "graph-1",
      state: "planning",
      subtasks: null,
      detail: null,
      queued_task_id: null,
    },
  };
}

function lead(id: string): ServerEvent {
  return {
    seq: 1,
    ts,
    type: "lead.status",
    payload: {
      agent_id: id,
      revision: 1,
      task: "Coordinate the task",
      model: "fake",
      state: "idle",
      step: 0,
      detail: null,
      limits: null,
      requested_plan_id: null,
    },
  };
}

function envelopes(...tail: ServerEvent[]): ServerEvent[] {
  return [{ ...opening, ts }, ...tail.map((event, index) => ({ ...event, seq: index + 2 }))];
}

function replay(events: readonly ServerEvent[]) {
  return events.reduce((state, event) => {
    const next = applyEvent(state, parseEvent(JSON.stringify(event)));
    expect(next.gap).toBe(false);
    return next;
  }, createInitialState());
}

const teamsOf = (...tail: ServerEvent[]) => selectTeams(replay(envelopes(...tail)));

it("retains unknown lead limits as null in team evidence", () => {
  const state = replay(envelopes(lead("agent-1")));
  const event = state.decisionEvents.find((event) => event.type === "lead.status");
  expect(event?.payload.limits).toBeNull();
  expect(selectTeams(state).byAgent["agent-1"]).toEqual({ team: "agent-1", kind: "lead" });
});

it("has no teams before any identity is observed", () => {
  expect(selectTeams(createInitialState())).toEqual({ byAgent: {}, teamOrder: [] });
  expect(teamsOf(plan("plan-1"))).toEqual({ byAgent: {}, teamOrder: [] });
});

it("gives a person-started agent its own team and ignores status.started_by", () => {
  expect(teamsOf(status("agent-1", null, { started_by: "agent-9" }))).toEqual({
    byAgent: { "agent-1": { team: "agent-1", kind: "agent" } },
    teamOrder: ["agent-1"],
  });
});

it("uses a plan without a lead as the team", () => {
  expect(teamsOf(plan("plan-1"), status("agent-1", "plan-1"))).toEqual({
    byAgent: { "agent-1": { team: "plan-1", kind: "plan" } },
    teamOrder: ["plan-1"],
  });
});

it("resolves a referenced plan even before any plan update exists", () => {
  expect(teamsOf(status("agent-1", "plan-1")).byAgent["agent-1"]).toEqual({
    team: "plan-1",
    kind: "plan",
  });
});

it("combines two plans of one lead and includes the lead itself", () => {
  expect(
    teamsOf(
      status("agent-1", "plan-1"),
      status("agent-2", "plan-2"),
      plan("plan-1", "agent-9"),
      plan("plan-2", "agent-9"),
    ),
  ).toEqual({
    byAgent: {
      "agent-1": { team: "agent-9", kind: "lead" },
      "agent-2": { team: "agent-9", kind: "lead" },
      "agent-9": { team: "agent-9", kind: "lead" },
    },
    teamOrder: ["agent-9"],
  });
});

it("keeps the first non-null status plan after a conflicting status", () => {
  const state = replay(envelopes(status("agent-1", "plan-2"), status("agent-1", "plan-1")));
  expect(state.agents["agent-1"]?.plan_id).toBe("plan-1");
  expect(selectTeams(state).byAgent["agent-1"]).toEqual({ team: "plan-2", kind: "plan" });
});

it("lets null precede the first plan and never erase that assignment later", () => {
  expect(
    teamsOf(
      status("agent-1"),
      status("agent-1", "plan-2"),
      status("agent-1"),
      status("agent-1", "plan-1"),
    ).byAgent["agent-1"],
  ).toEqual({ team: "plan-2", kind: "plan" });
});

it("ignores a planner's plan-tagged tick without changing any agent's team", () => {
  const state = replay(envelopes(status("agent-1", "plan-1"), plan("plan-1", "agent-9")));
  const before = selectTeams(state);
  const tick: ServerEvent = {
    seq: state.lastSeq + 1,
    ts,
    type: "usage.tick",
    payload: {
      agent_id: "agent-20",
      plan_id: "plan-2",
      role: "plan",
      model: "fake",
      change_id: null,
      input_tokens: 1,
      output_tokens: 1,
      cached_tokens: 0,
      cost_usd: 0,
      spent_usd: 0,
      budget_usd: 150,
      brake: "ok",
    },
  };
  const after = applyEvent(state, parseEvent(JSON.stringify(tick)));
  expect(after.gap).toBe(false);
  expect(selectTeams(after)).toEqual(before);
  expect(selectTeams(after).byAgent).not.toHaveProperty("agent-20");
});

it("replaces plan ownership then clears it, retaining all former leads", () => {
  const source = envelopes(
    status("agent-1", "plan-1"),
    plan("plan-1", "agent-9"),
    plan("plan-1", "agent-8", 2),
    plan("plan-1", null, 3),
  );
  const changed = selectTeams(replay(source.slice(0, -1)));
  expect(changed.byAgent["agent-1"]).toEqual({ team: "agent-8", kind: "lead" });
  expect(changed.byAgent["agent-9"]).toEqual({ team: "agent-9", kind: "lead" });
  expect(selectTeams(replay(source))).toEqual({
    byAgent: {
      "agent-1": { team: "plan-1", kind: "plan" },
      "agent-9": { team: "agent-9", kind: "lead" },
      "agent-8": { team: "agent-8", kind: "lead" },
    },
    teamOrder: ["plan-1", "agent-9", "agent-8"],
  });
});

it.each([1, 2])("uses event order even when the latest plan revision is %i", (revision) => {
  const teams = teamsOf(
    status("agent-1", "plan-1"),
    plan("plan-1", "agent-9", 2),
    plan("plan-1", "agent-8", revision),
  );
  expect(teams.byAgent["agent-1"]).toEqual({ team: "agent-8", kind: "lead" });
  expect(teams.byAgent["agent-9"]).toEqual({ team: "agent-9", kind: "lead" });
});

it("includes an idle lead observed only through lead.status", () => {
  expect(teamsOf(lead("agent-9"))).toEqual({
    byAgent: { "agent-9": { team: "agent-9", kind: "lead" } },
    teamOrder: ["agent-9"],
  });
});

it("includes a lead observed only as a plan's started_by without coding agents", () => {
  expect(teamsOf(plan("plan-1", "agent-9"))).toEqual({
    byAgent: { "agent-9": { team: "agent-9", kind: "lead" } },
    teamOrder: ["agent-9"],
  });
});

it.each(["lead.status", "plan.updated"])(
  "lets %s override a lead's earlier plan assignment",
  (source) => {
    const discovery = source === "lead.status" ? lead("agent-1") : plan("plan-2", "agent-1");
    expect(
      teamsOf(plan("plan-1", "agent-9"), status("agent-1", "plan-1"), discovery).byAgent["agent-1"],
    ).toEqual({ team: "agent-1", kind: "lead" });
  },
);

it("orders distinct resolved teams by first occurrence, independently of ids, timestamps and slots", () => {
  const teams = teamsOf(
    { ...status("agent-10", null, { slot: "F" }), ts: "2026-11-02T15:00:10.000Z" },
    { ...status("agent-2"), ts: "2026-11-02T15:00:09.000Z" },
    status("agent-30", "plan-1"),
    lead("agent-8"),
    plan("plan-1", "agent-9"),
    status("agent-3", "plan-2"),
    plan("plan-2", "agent-9"),
    lead("agent-8"),
    status("agent-10"),
  );
  expect(teams.teamOrder).toEqual(["agent-10", "agent-2", "agent-9", "agent-8"]);
  expect(Object.keys(teams.byAgent)).toEqual([
    "agent-10",
    "agent-2",
    "agent-30",
    "agent-8",
    "agent-9",
    "agent-3",
  ]);
});

it("rebuilds a complete mid-run prefix and resumes identically to uninterrupted reduction", () => {
  const source = envelopes(
    status("agent-1", "plan-1"),
    { ...plan("plan-1", "agent-9"), ts: "2026-11-02T15:00:01.000Z" },
    { ...plan("plan-1", "agent-8", 2), ts: "2026-11-02T15:00:02.000Z" },
    { ...status("agent-2", "plan-1"), ts: "2026-11-02T15:00:03.000Z" },
  );
  // replayTimeline reconstructs the complete accepted prefix before resuming its suffix.
  const prefix = replay(source.slice(0, 3));
  expect(prefix.lastSeq).toBe(3);
  expect(selectTeams(prefix).byAgent["agent-1"]).toEqual({ team: "agent-9", kind: "lead" });
  const continued = source.slice(prefix.lastSeq).reduce(applyEvent, prefix);
  expect(continued.gap).toBe(false);
  expect(selectTeams(continued)).toEqual(selectTeams(replay(source)));
});

it("seeks backward by rebuilding the prefix without retaining future ownership", () => {
  const source = envelopes(status("agent-1", "plan-1"), {
    ...plan("plan-1", "agent-9"),
    ts: "2026-11-02T15:00:01.000Z",
  });
  expect(selectTeams(replay(source)).teamOrder).toEqual(["agent-9"]);
  expect(selectTeams(replay(source.slice(0, 2)))).toEqual({
    byAgent: { "agent-1": { team: "plan-1", kind: "plan" } },
    teamOrder: ["plan-1"],
  });
});

function freeze(value: unknown): void {
  if (value !== null && typeof value === "object") {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
}

it("projects deeply frozen input without mutation", () => {
  const state = replay(envelopes(status("agent-1", "plan-1"), plan("plan-1", "agent-9")));
  const before = JSON.stringify(state);
  freeze(state);
  expect(selectTeams(state).byAgent["agent-1"]).toEqual({ team: "agent-9", kind: "lead" });
  expect(JSON.stringify(state)).toBe(before);
});

it("returns the same cached identity for repeated calls on an immutable state", () => {
  const state = replay(envelopes(status("agent-1")));
  expect(selectTeams(state)).toBe(selectTeams(state));
});

it("protects cached output and its nested identities from caller mutation", () => {
  const state = replay(envelopes(status("agent-1")));
  const teams = selectTeams(state);
  const identity = teams.byAgent["agent-1"];
  if (!identity) throw new Error("Missing identity");
  expect(Reflect.set(identity, "team", "agent-9")).toBe(false);
  expect(Reflect.set(teams.byAgent, "agent-9", identity)).toBe(false);
  expect(Reflect.set(teams.teamOrder, "0", "agent-9")).toBe(false);
  expect(Reflect.set(teams, "teamOrder", [])).toBe(false);
  expect(selectTeams(state)).toEqual({
    byAgent: { "agent-1": { team: "agent-1", kind: "agent" } },
    teamOrder: ["agent-1"],
  });
});

it("preserves independent cached values across interleaved prefixes", () => {
  const source = envelopes(status("agent-1", "plan-1"), plan("plan-1", "agent-9"));
  const past = replay(source.slice(0, -1));
  const future = replay(source);
  const before = selectTeams(past);
  const after = selectTeams(future);
  expect(before.byAgent["agent-1"]).toEqual({ team: "plan-1", kind: "plan" });
  expect(after.byAgent["agent-1"]).toEqual({ team: "agent-9", kind: "lead" });
  expect(selectTeams(past)).toBe(before);
  expect(selectTeams(future)).toBe(after);
});

it("resets membership, assignment and order on a new run", () => {
  const old = replay(envelopes(status("agent-1", "plan-1"), plan("plan-1", "agent-9")));
  const before = selectTeams(old);
  const restarted = applyEvent(old, {
    ...opening,
    payload: { ...opening.payload, run_id: "run-next" },
  });
  expect(restarted.gap).toBe(false);
  expect(selectTeams(restarted)).toEqual({ byAgent: {}, teamOrder: [] });
  const next = applyEvent(restarted, { ...status("agent-1"), seq: 2 });
  expect(selectTeams(next)).toEqual({
    byAgent: { "agent-1": { team: "agent-1", kind: "agent" } },
    teamOrder: ["agent-1"],
  });
  expect(selectTeams(old)).toBe(before);
});
