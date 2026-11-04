import { expect, it } from "vitest";
import type { ServerEvent } from "@contracts";
import log from "../../../fixtures/events/stage-clocks.jsonl?raw";
import { parseEvent } from "../transport";
import { foldStages } from "./stageFold";
import { applyEvent } from "./applyEvent";
import { createInitialState } from "./types";

const fixture = log.trim().split("\n").map(parseEvent);
type Row = [ServerEvent["type"], number | string, Record<string, unknown>?];
function events(...rows: Row[]): ServerEvent[] {
  return rows.map(([type, ms, overrides = {}], index) => {
    const template = fixture.find((event) => event.type === type);
    if (!template) throw new Error(`Missing ${type} template`);
    return {
      ...structuredClone(template),
      seq: index + 1,
      ts: typeof ms === "string" ? ms : new Date(Date.UTC(2026, 10, 4) + ms).toISOString(),
      payload: {
        ...structuredClone(template.payload),
        ...("change_id" in template.payload ? { change_id: "change-1" } : {}),
        ...("revision" in template.payload ? { revision: 1 } : {}),
        ...overrides,
      },
    } as ServerEvent;
  });
}
const proposed: Row = ["change.proposed", 0];
const receipt: Row = ["change.facts_recorded", 10];
const resolved = (overrides = {}): Row => [
  "change.resolved",
  40,
  { decision: "rejected", decided_by: "person", ...overrides },
];

it("preserves revised work, overlapping test phases and combined subtraction in the fixture", () => {
  expect(foldStages(fixture, "change-1")).toEqual({
    agent_work: { ms: 150000, known_ms: 150000, intervals: 2, open: false },
    checks: { ms: 30000, known_ms: 30000, intervals: 2, open: false },
    waiting_for_person: { ms: 90000, known_ms: 90000, intervals: 2, open: false },
    test_runs: { ms: 60000, known_ms: 60000, intervals: 5, open: false },
    test_phases: [
      { phase: "agent", ms: 15000, runs: 1 },
      { phase: "proposal", ms: 5000, runs: 1 },
      { phase: "combined", ms: 20000, runs: 1 },
      { phase: "base_probe", ms: 10000, runs: 1 },
      { phase: "head_probe", ms: 10000, runs: 1 },
    ],
    problems: [],
  });
});

it.each([[], [2], [1, 3], [1, 1], [2, 1], [1, 1.5], [1, "2"], [true]])(
  "refuses incomplete or noninteger inventories %j",
  (...seqs) => {
    const template = fixture[0];
    if (!template) throw new Error("Missing envelope");
    expect(
      foldStages(
        seqs.map((seq) => ({ ...template, seq }) as ServerEvent),
        "change-1",
      ),
    ).toBeNull();
  },
);

it("counts matching changes only, closes paused/halted work and uses unrelated timestamps", () => {
  const log = events(
    ["agent.status", 0, { state: "starting" }],
    ["agent.status", 2, { state: "working" }],
    ["agent.status", 5, { state: "done", change_id: "change-2" }],
    ["agent.status", 10, { state: "paused" }],
    ["agent.status", 20, { state: "revising" }],
    ["agent.status", 25, { state: "halted" }],
    ["agent.status", 30, { state: "working" }],
    ["agent.status", 50, { state: "done", change_id: "change-2" }],
  );
  const before = structuredClone(log);
  expect(foldStages(log, "change-1")?.agent_work).toEqual({
    ms: 35,
    known_ms: 35,
    intervals: 3,
    open: true,
  });
  expect(foldStages(log, "change-1")).toEqual(foldStages(log, "change-1"));
  expect(log).toEqual(before);
});

it.each(["change.facts_recorded", "change.reviewed", "change.assessed"] as const)(
  "uses the last matching %s receipt and keeps wrong revisions outside the window",
  (kind) => {
    const stages = foldStages(
      events(
        proposed,
        receipt,
        [kind, 20],
        [kind, 25, { revision: 2 }],
        [kind, 30, { change_id: "change-2" }],
        resolved(),
        [kind, 50],
      ),
      "change-1",
    );
    expect(stages?.checks.ms).toBe(20);
    expect(stages?.waiting_for_person.ms).toBe(20);
  },
);

it.each(["rules", "cross_review", "lead_agent", null, "person"])(
  "uses the recorded decider %s without inference",
  (decided_by) => {
    const stage = foldStages(
      events(proposed, receipt, resolved({ decided_by })),
      "change-1",
    )?.waiting_for_person;
    expect(stage).toMatchObject({
      ms: decided_by === null ? null : decided_by === "person" ? 30 : 0,
      intervals: decided_by === null || decided_by === "person" ? 1 : 0,
      open: false,
    });
  },
);

it("keeps replaced revisions unknown, counts the next wait and ignores unpaired resolutions", () => {
  const stages = foldStages(
    events(
      proposed,
      receipt,
      ["change.proposed", 20, { revision: 2 }],
      resolved(),
      resolved({ revision: 2 }),
      resolved({ revision: 2 }),
    ),
    "change-1",
  );
  expect(stages?.waiting_for_person).toEqual({ ms: null, known_ms: 20, intervals: 2, open: false });
  expect(stages?.problems).toEqual(["Revision 1 replaced without a decision at seq 3."]);
});

it("distinguishes open checks, open waits and absent checks without a receipt", () => {
  expect(
    foldStages(events(proposed, ["agent.status", 50, { change_id: "other" }]), "change-1")?.checks,
  ).toEqual({ ms: 50, known_ms: 50, intervals: 1, open: true });
  expect(
    foldStages(events(proposed, receipt, ["agent.status", 50, { change_id: "other" }]), "change-1")
      ?.waiting_for_person,
  ).toEqual({ ms: 40, known_ms: 40, intervals: 1, open: true });
  expect(foldStages(events(proposed, resolved()), "change-1")?.checks.intervals).toBe(0);
});

it.each(["approved", "changes_requested", "rejected"] as const)(
  "subtracts only the last matching combined suite for person approval: %s",
  (decision) => {
    const stages = foldStages(
      events(
        ["tests.executed", 0, { phase: "combined", suite: { duration_ms: 500 } }],
        proposed,
        receipt,
        ["tests.executed", 15, { phase: "combined", suite: { duration_ms: 15 } }],
        ["tests.executed", 20, { phase: "combined", suite: { duration_ms: 10 } }],
        ["tests.executed", 25, { phase: "combined", revision: 2, suite: { duration_ms: 500 } }],
        ["tests.executed", 26, { phase: "proposal", suite: { duration_ms: 500 } }],
        resolved({ decision }),
        ["tests.executed", 45, { phase: "combined", suite: { duration_ms: 500 } }],
      ),
      "change-1",
    );
    expect(stages?.waiting_for_person.ms).toBe(decision === "approved" ? 20 : 30);
    expect(stages?.test_runs.ms).toBe(2025);
  },
);

it.each([-1, 30, 31])(
  "preserves negative, genuine zero and excessive subtraction: %s",
  (duration_ms) => {
    const stages = foldStages(
      events(
        proposed,
        receipt,
        ["tests.executed", 20, { phase: "combined", suite: { duration_ms } }],
        resolved({ decision: "approved" }),
      ),
      "change-1",
    );
    expect(stages?.waiting_for_person.ms).toBe(duration_ms === 30 ? 0 : null);
    expect(stages?.test_runs).toMatchObject({
      ms: duration_ms < 0 ? null : duration_ms,
      intervals: 1,
    });
  },
);

it.each(["invalid", "2026-02-30T00:00:00.000Z", "2026-11-03T00:00:00.000Z"])(
  "preserves invalid/backwards intervals as unknown: %s",
  (ts) => {
    const stages = foldStages(
      events(["agent.status", 0, { state: "working" }], ["agent.status", ts, { state: "done" }]),
      "change-1",
    );
    expect(stages?.agent_work.ms).toBeNull();
    expect(stages?.problems).toEqual(["Timestamp goes back or is invalid at seq 2."]);
  },
);

it("accepts microseconds, floors them, and cites an invalid start before an invalid end", () => {
  expect(
    foldStages(
      events(
        ["agent.status", "2026-11-04T00:00:00.000999Z", { state: "working" }],
        ["agent.status", "2026-11-04T00:00:00.001001Z", { state: "done" }],
      ),
      "change-1",
    )?.agent_work.ms,
  ).toBe(1);
  expect(
    foldStages(events(["agent.status", "invalid", { state: "working" }]), "change-1")?.problems,
  ).toEqual(["Timestamp goes back or is invalid at seq 1."]);
});

it("caps problems at twenty in sequence order and retains unknown lower bounds", () => {
  const rows: Row[] = Array.from(
    { length: 21 },
    (_, n) =>
      [
        ["change.proposed", n],
        ["change.resolved", n, { decided_by: null }],
      ] satisfies Row[],
  ).flat();
  const stages = foldStages(events(...rows), "change-1");
  expect(stages?.waiting_for_person).toMatchObject({ ms: null, known_ms: 0, intervals: 21 });
  expect(stages?.problems).toEqual(
    Array.from(
      { length: 20 },
      (_, n) => `No recorded decider for revision 1 at seq ${String(n * 2 + 2)}.`,
    ),
  );
});

it("counts confirmed revision resolutions only and never retains a rejected envelope", () => {
  const state = fixture.slice(0, 18).reduce(applyEvent, createInitialState());
  expect(state.changes["change-1"]?.revisionRounds).toBe(1);
  const end = fixture[18];
  if (!end) throw new Error("Missing envelope");
  const gap = applyEvent(state, { ...end, seq: end.seq + 1 });
  expect(gap.stageEvents).toBe(state.stageEvents);
  expect(gap.changes).toBe(state.changes);
  expect(applyEvent(gap, end).stageEvents).toHaveLength(19);
});
