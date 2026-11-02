import type { AgentStatus, LeadStatus, Plan, ServerEvent } from "@contracts";
import { expect, it } from "vitest";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { applyEvent, createInitialState } from "../state";
import { parseEvent } from "../transport";
import { selectPresentation } from "./presentation";

const opening = parseEvent(log.trim().split("\n")[0] ?? "");
const ts = "2026-11-02T15:00:00Z";
function event<T extends ServerEvent["type"]>(
  type: T,
  payload: Extract<ServerEvent, { type: T }>["payload"],
): ServerEvent {
  return { type, payload, seq: 0, ts } as ServerEvent;
}
function agent(id: string, extra: Partial<AgentStatus> = {}) {
  return event("agent.status", {
    agent_id: id,
    change_id: `change-${id}`,
    slot: "A",
    task: "Handwritten identity test",
    model: "fake",
    state: "working",
    step: 0,
    max_steps: 30,
    detail: null,
    plan_id: null,
    zone: null,
    started_by: null,
    queued_task_id: null,
    base_commit: null,
    examples: null,
    answering: null,
    ...extra,
  });
}
function lead(id: string, extra: Partial<LeadStatus> = {}) {
  return event("lead.status", {
    agent_id: id,
    revision: 1,
    task: "Coordinate",
    model: "fake",
    state: "working",
    step: 0,
    detail: null,
    limits: null,
    requested_plan_id: null,
    ...extra,
  });
}
function plan(id: string, lead: string | null, ids: (string | null)[]): ServerEvent {
  return event("plan.updated", {
    plan_id: id,
    revision: 1,
    started_by: lead,
    task: "Handwritten plan",
    graph_version: "fixture",
    state: "planning",
    detail: null,
    queued_task_id: null,
    subtasks: ids.map((agent_id) => ({
      task: "Subtask",
      zone: { node_ids: [], paths: [] },
      depends_on: [],
      agent_id,
      state: agent_id ? "running" : "waiting",
    })),
  } satisfies Plan);
}
function resolve(id: string, conflict = false): ServerEvent {
  return event("change.resolved", {
    change_id: `change-${id}`,
    agent_id: id,
    revision: 1,
    decision: "approved",
    reason: null,
    comment: null,
    merge: conflict ? "conflict" : "merged",
    merge_commit: conflict ? null : "abcdef0",
    conflict: conflict ? { kind: "merge", paths: ["example.py"], tests: null } : null,
    decided_by: "person",
    review_seq: null,
    requested_by: null,
    head_commit: null,
    reasons: [],
    assessment: null,
    answers: null,
  });
}
const fold = (...tail: ServerEvent[]) =>
  [opening, ...tail].reduce(
    (state, e, i) => applyEvent(state, parseEvent(JSON.stringify({ ...e, seq: i + 1 }))),
    createInitialState(),
  );
const identities = (...tail: ServerEvent[]) => selectPresentation(fold(...tail)).byAgent;

it("shares lead identity across plans and slots while retaining each plan's indices", () => {
  const rows = identities(
    plan("p", "lead", ["one", "two"]),
    agent("two", { plan_id: "p", slot: "F" }),
    agent("one", { plan_id: "p", slot: "B" }),
    plan("q", "lead", ["three"]),
    agent("three", { plan_id: "q" }),
  );
  expect(rows.one).toMatchObject({ team: "lead", label: "A·1", hue: "A", planId: "p" });
  expect(rows.two).toMatchObject({ team: "lead", label: "A·2", hue: "A" });
  expect(rows.three).toMatchObject({ label: "A·1", planId: "q" });
  expect(rows.lead).toMatchObject({ kind: "lead", label: "A" });
});
it("uses leadless plan indices, including above six, with waiting siblings", () => {
  const rows = identities(
    plan("p", null, [null, null, null, null, null, null, "seven"]),
    agent("seven", { plan_id: "p" }),
  );
  expect(rows.seven).toMatchObject({ team: "p", kind: "plan", label: "A·7" });
});
it("keeps lone C until its plan supplies a sibling, independent of running count", () => {
  const prefix = [agent("one"), agent("two"), agent("person")];
  expect(identities(...prefix).person?.label).toBe("C");
  expect(
    identities(...prefix, plan("p", null, ["person"]), agent("person", { plan_id: "p" })).person
      ?.label,
  ).toBe("C");
  expect(
    identities(...prefix, plan("p", null, ["person", null]), agent("person", { plan_id: "p" }))
      .person?.label,
  ).toBe("C·1");
});
it("allocates a lead-only team without inventing a coding agent", () => {
  const state = fold(
    event("lead.status", {
      agent_id: "lead",
      revision: 1,
      task: "Coordinate",
      model: "fake",
      state: "idle",
      step: 0,
      detail: null,
      limits: null,
      requested_plan_id: null,
    }),
  );
  expect(selectPresentation(state).byAgent.lead).toMatchObject({
    kind: "lead",
    letter: "A",
    hue: "A",
  });
  expect(state.agents).toEqual({});
});
it("never names a new agent after the reused capacity slot", () => {
  const rows = identities(agent("old", { slot: "F" }), resolve("old"), agent("new", { slot: "F" }));
  expect(rows.old?.label).toBe("A");
  expect(rows.new?.label).toBe("B");
});
const six = () => Array.from({ length: 6 }, (_, i) => agent(`a${String(i)}`));
it("seventh team skips the oldest pending donor and keeps both identities", () => {
  const rows = identities(
    ...six(),
    agent("a1", { state: "ready_for_review" }),
    { ...resolve("a2"), ts: "2026-11-02T15:01:00Z" },
    { ...resolve("a0"), ts: "2026-11-02T15:02:00Z" },
    agent("new"),
  );
  expect(rows.new).toMatchObject({ label: "G", hue: "C" });
  expect(rows.a2).toMatchObject({ label: "C", hue: "C" });
});
it("all-pending fallback uses longest idle, including conflicts", () => {
  const rows = identities(
    ...six(),
    agent("a3", { state: "paused" }),
    resolve("a3", true),
    { ...agent("a0", { state: "ready_for_review" }), ts: "2026-11-02T15:02:00Z" },
    agent("new"),
  );
  expect(rows.new).toMatchObject({ label: "G", hue: "D" });
});
it("ties use first-observed team order and active teams follow idle teams", () => {
  expect(identities(...six(), agent("new")).new?.hue).toBe("A");
  expect(identities(...six(), resolve("a2"), resolve("a0"), agent("new")).new?.hue).toBe("A");
});
it("event time determines idle ordering before sequence breaks equal-time ties", () => {
  const later = { ...resolve("a0"), ts: "2026-11-02T15:02:00Z" };
  const earlier = { ...resolve("a2"), ts: "2026-11-02T15:01:00Z" };
  expect(identities(...six(), earlier, later, agent("new")).new?.hue).toBe("C");
});
it("returning donors keep their hue while another team shares it", () => {
  const rows = identities(...six(), resolve("a0"), agent("new"), agent("a0", { state: "working" }));
  expect(rows.a0).toMatchObject({ label: "A", hue: "A" });
  expect(rows.new).toMatchObject({ label: "G", hue: "A" });
});
it("reconstructs interleaved replay prefixes, seeks backward and resets on a new run", () => {
  const past = fold(agent("one")),
    future = fold(agent("one"), agent("two"));
  const before = selectPresentation(past);
  expect(selectPresentation(future).byAgent.two?.label).toBe("B");
  expect(selectPresentation(past)).toBe(before);
  if (opening.type !== "run.started") throw new Error("Missing run");
  const reset = applyEvent(future, {
    ...opening,
    seq: 1,
    payload: { ...opening.payload, run_id: "new-run" },
  });
  expect(selectPresentation(reset).byAgent).toEqual({});
  expect(
    selectPresentation(applyEvent(reset, { ...agent("two"), seq: 2 })).byAgent.two?.label,
  ).toBe("A");
});
it("does not mutate deeply frozen inputs or expose mutable cached identities", () => {
  const state = fold(agent("one"));
  const before = JSON.stringify(state);
  function freeze(value: unknown) {
    if (value && typeof value === "object") {
      Object.values(value).forEach(freeze);
      Object.freeze(value);
    }
  }
  freeze(state);
  const result = selectPresentation(state);
  expect(JSON.stringify(state)).toBe(before);
  expect(Reflect.set(result.byAgent.one ?? {}, "label", "X")).toBe(false);
  expect(Reflect.set(result.byAgent, "other", {})).toBe(false);
});

it.each(["starting", "working", "idle"] as const)(
  "keeps an open lead hue occupied while %s (migrated from Chat)",
  (state) => {
    const rows = identities(
      lead("lead", { state }),
      ...Array.from({ length: 5 }, (_, i) => agent(`a${String(i)}`)),
      resolve("a0"),
      agent("new"),
    );
    expect(rows.lead).toMatchObject({ label: "A", hue: "A", kind: "lead" });
    expect(rows.new?.hue).toBe("B");
  },
);
it("introduces a lead before its children and breaks equal idle times by first observation", () => {
  const prefix = [
    lead("first"),
    lead("second"),
    plan("p", "first", ["one", "two"]),
    agent("two", { plan_id: "p", slot: "F" }),
    agent("one", { plan_id: "p", slot: "B" }),
    ...Array.from({ length: 4 }, (_, i) => agent(`a${String(i)}`)),
    lead("second", { revision: 2, state: "stopped" }),
    lead("first", { revision: 2, state: "stopped" }),
    resolve("one"),
    resolve("two"),
  ];
  const rows = identities(...prefix, agent("new"));
  expect(rows.first).toMatchObject({ label: "A", hue: "A", kind: "lead" });
  expect(rows.second).toMatchObject({ label: "B", hue: "B", kind: "lead" });
  expect(rows.one).toMatchObject({ label: "A·1", hue: "A" });
  expect(rows.two).toMatchObject({ label: "A·2", hue: "A" });
  expect(rows.new).toMatchObject({ label: "G", hue: "A" });
  // A stale idle revision cannot reserve the terminal lead's hue again.
  expect(identities(...prefix, lead("first", { state: "idle" }), agent("new")).new?.hue).toBe("A");
});
