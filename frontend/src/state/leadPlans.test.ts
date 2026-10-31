import type { ServerEvent } from "@contracts";
import { expect, it } from "vitest";
import log from "../../../fixtures/events/handwritten-lead-plans.jsonl?raw";
import legacy from "../../../fixtures/events/handwritten-inbox-kinds.jsonl?raw";
import { parseEvent } from "../transport";
import { applyEvent, createInitialState, selectDecisionInbox } from "./index";

const events = log.trim().split("\n").map(parseEvent);
const replay = (source = events, end = source.length) =>
  source.slice(0, end).reduce(applyEvent, createInitialState());
const entries = (source = events, end = source.length) =>
  selectDecisionInbox(replay(source, end))
    .flatMap((entry) => entry.decisions)
    .filter((entry) => entry.kind === "lead_plan");

it("keeps three requested revisions with distinct identities, plan ids and first evidence", () => {
  const open = entries(events, 8);
  expect(open.map((entry) => entry.planId)).toEqual(["plan-1", "plan-2", "plan-3"]);
  expect(new Set(open.map((entry) => entry.id)).size).toBe(3);
  expect(open.map((entry) => entry.identity)).toEqual(
    [2, 3, 4].map((revision, index) => ({
      kind: "lead_plan",
      agent_id: "agent-1",
      revision,
      evidence_seq: 4 + index * 2,
    })),
  );
  expect(open.map((entry) => entry.reason)).toEqual([
    "Plan 1: update pricing",
    "Plan 2: update inventory",
    "Plan 3: update orders",
  ]);
  expect(entries(events, 9)[0]?.identity).toEqual(open[0]?.identity);
});

it("starting plan 2 closes only revision 3", () => {
  expect(entries(events, 9).map((entry) => entry.planId)).toEqual(["plan-1", "plan-3"]);
});

it("rejecting plan 1 closes only revision 2", () => {
  expect(entries(events, 10).map((entry) => entry.planId)).toEqual(["plan-3"]);
});

it("a later null request closes nothing and preserves evidence", () => {
  expect(entries()).toEqual(entries(events, 10));
  expect(entries()[0]?.identity).toMatchObject({ revision: 4, evidence_seq: 8 });
});

it("never opens a plan started by another lead", () => {
  const foreign = events.map((event): ServerEvent =>
    event.type === "plan.updated"
      ? { ...event, payload: { ...event.payload, started_by: "agent-2" } }
      : event,
  );
  expect(entries(foreign, 8)).toEqual([]);
});

it("a new run clears all open plans", () => {
  const first = events[0];
  if (first?.type !== "run.started") throw new Error("Missing run");
  const state = applyEvent(replay(events, 8), {
    ...first,
    seq: 1,
    payload: { ...first.payload, run_id: "run-20261031-190000" },
  });
  expect(selectDecisionInbox(state)).toEqual([]);
});

it("keeps the legacy latest-revision and single-proposed-plan rule with missing or null ids", () => {
  const older = legacy.trim().split("\n").map(parseEvent);
  const oldPlan = older.find((event) => event.type === "plan.updated");
  if (oldPlan?.type !== "plan.updated") throw new Error("Missing legacy plan");
  for (const source of [
    older,
    older.map((event): ServerEvent =>
      event.type === "lead.status"
        ? { ...event, payload: { ...event.payload, requested_plan_id: null } }
        : event,
    ),
  ]) {
    expect(entries(source)).toMatchObject([
      { planId: "plan-1", identity: { agent_id: "agent-5", revision: 4, evidence_seq: 12 } },
    ]);
    expect(
      entries([
        ...source,
        {
          ...oldPlan,
          seq: source.length + 1,
          payload: { ...oldPlan.payload, plan_id: "plan-2" },
        },
      ]),
    ).toEqual([]);
  }
  const extra = events
    .slice(2, 8)
    .map((event) => parseEvent(JSON.stringify(event).replaceAll("plan-", "plan-1")));
  const mixed = [...older, ...extra].map((event, index) => ({
    ...event,
    seq: index + 1,
  }));
  expect(entries(mixed)).toHaveLength(4);
});

it("ignores stale plan revisions, retains opening evidence and uses the fallback text", () => {
  const proposal = events[7];
  if (proposal?.type !== "plan.updated") throw new Error("Missing plan");
  const updated: ServerEvent = {
    ...proposal,
    seq: 9,
    payload: { ...proposal.payload, revision: 2, detail: null },
  };
  const source = [...events.slice(0, 8), updated, { ...proposal, seq: 10 }];
  expect(entries(source)[2]).toMatchObject({
    reason: "Lead plan awaits approval",
    firstSeq: 8,
    identity: { evidence_seq: 8 },
  });
});
