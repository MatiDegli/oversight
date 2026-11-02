import type { Plan, ServerEvent } from "@contracts";
import { expect, it } from "vitest";
import { applyEvent, createInitialState } from "../state";
import log from "../../../fixtures/events/handwritten-lead-plans.jsonl?raw";
import { parseEvent } from "../transport";
const leadActionsBase = log.trim().split("\n").map(parseEvent).slice(0, 8);
const proposal = leadActionsBase.findLast((e) => e.type === "plan.updated");
if (proposal?.type !== "plan.updated") throw new Error("Missing plan");
const orderPlan = proposal.payload;
const cartPlan = { ...orderPlan, plan_id: "unlinked" };
const queueItem: import("@contracts").QueuedTask = {
  queued_task_id: "queue-test",
  revision: 7,
  state: "queued",
  plan_id: orderPlan.plan_id,
  plan_revision: 3,
  agent: null,
  agent_ids: [],
  detail: null,
  started_by: null,
};
import {
  dependencyWords,
  firstWaveSize,
  planAttemptPending,
  planFits,
  selectPlanEvidence,
  selectPlanQueue,
  selectRetainedPlans,
} from "./planSelectors";

it("ends a Queue attempt when newer plan evidence leaves it proposed and unlinked", () => {
  const state = leadActionsBase.reduce(applyEvent, createInitialState());
  const attempt = {
    run: state.run,
    planId: orderPlan.plan_id,
    action: "queue" as const,
    revision: 2,
    phase: "pending" as const,
  };
  for (const revision of [1, 2]) {
    expect(planAttemptPending(state, { ...orderPlan, revision }, undefined, attempt)).toBe(true);
  }
  const revised = { ...orderPlan, revision: 3, state: "proposed" as const, queued_task_id: null };
  expect(planAttemptPending(state, revised, undefined, attempt)).toBe(false);
  // Superseding a Queue attempt does not settle a pending rejection.
  expect(planAttemptPending(state, revised, undefined, { ...attempt, action: "reject" })).toBe(
    true,
  );
});

it("selects independent revisions with timestamps, joins only linked queues and retains failures", () => {
  let state = leadActionsBase.reduce(applyEvent, createInitialState());
  const add = (
    type: "plan.updated" | "queue.updated",
    payload: typeof orderPlan | typeof queueItem,
  ) => {
    state = applyEvent(state, {
      seq: state.lastSeq + 1,
      ts: "2026-11-01T19:00:00Z",
      type,
      payload,
    } as import("@contracts").ServerEvent);
  };
  add("plan.updated", { ...orderPlan, revision: 3, queued_task_id: queueItem.queued_task_id });
  add("queue.updated", queueItem);
  add("queue.updated", { ...queueItem, revision: 6, state: "failed" });
  add("plan.updated", { ...orderPlan, revision: 2 });
  expect(selectPlanEvidence(state).plans.get(orderPlan.plan_id)?.ts).toBe("2026-11-01T19:00:00Z");
  expect(
    selectPlanQueue(state, { ...orderPlan, queued_task_id: queueItem.queued_task_id })?.payload,
  ).toEqual(queueItem);
  expect(selectPlanQueue(state, cartPlan)).toBeUndefined();
  expect(selectRetainedPlans(state).queued).toHaveLength(1);
  add("plan.updated", { ...orderPlan, revision: 4, state: "failed", detail: "Dispatch failed" });
  expect(selectRetainedPlans(state).failed[0]?.payload.detail).toBe("Dispatch failed");
});
it("derives first-wave offered-slot admission and numbered prerequisite grammar", () => {
  const wave = { ...orderPlan, subtasks: [queueTask(), queueTask()] };
  expect(firstWaveSize(wave)).toBe(2);
  expect(planFits(wave, 2)).toBe(true);
  expect(planFits(wave, 1)).toBe(false);
  expect(firstWaveSize({ ...orderPlan, subtasks: null })).toBe(0);
  for (const [depends_on, words] of [
    [[], "Starts first"],
    [[0], "Starts after 1 merges"],
    [[0, 2], "Starts after 1 and 3 merge"],
  ] as const)
    expect(dependencyWords({ ...queueTask(), depends_on: [...depends_on] })).toBe(words);
});
const queueTask = () => ({
  task: "Task",
  zone: { node_ids: [], paths: [] },
  depends_on: [],
  state: null,
  agent_id: null,
});

const source = log.trim().split("\n").map(parseEvent);
const leads = leadActionsBase.filter((event) => event.type === "lead.status");
const lead = leads.at(-1);
if (!lead) throw new Error("Missing lead");
function retained(updates: ServerEvent[], base = leadActionsBase) {
  return selectRetainedPlans(
    [...base, ...updates]
      .map((event, index) => ({ ...event, seq: index + 1 }))
      .reduce(applyEvent, createInitialState()),
  );
}
const planUpdate = (plan: Partial<Plan>): ServerEvent => ({
  ...proposal,
  payload: { ...orderPlan, ...plan },
});

it("retains a real starting plan outside decisions while dispatching", () => {
  const result = retained([
    planUpdate({ revision: 2, state: "starting", queued_task_id: "queue-test" }),
  ]);
  expect(result.queued.map((event) => event.payload.state)).toEqual(["starting"]);
});

it("keeps all requested plans after a later null attachment and rejects a foreign author", () => {
  const result = retained([
    planUpdate({ revision: 2, queued_task_id: "queue-test" }),
    { ...lead, payload: { ...lead.payload, revision: 5, requested_plan_id: null } },
    planUpdate({ plan_id: "foreign", started_by: "another-lead", queued_task_id: "other" }),
    { ...lead, payload: { ...lead.payload, revision: 6, requested_plan_id: "foreign" } },
  ]);
  expect(result.queued.map((event) => event.payload.plan_id)).toEqual([orderPlan.plan_id]);
});

it("supports the unique legacy fallback without attributing multiple failed plans", () => {
  const legacy = [source[0], lead, proposal]
    .filter((event) => event !== undefined)
    .map((event) =>
      event.type === "lead.status"
        ? { ...event, payload: { ...event.payload, requested_plan_id: null } }
        : event,
    );
  expect(
    retained([planUpdate({ revision: 2, queued_task_id: "queue-test" })], legacy).queued,
  ).toHaveLength(1);
  const failed = planUpdate({ revision: 2, state: "failed" });
  expect(retained([failed], legacy).failed).toHaveLength(1);
  expect(
    retained([failed, planUpdate({ plan_id: "another", state: "failed" })], legacy).failed,
  ).toEqual([]);
  expect(
    retained([planUpdate({ plan_id: "another", queued_task_id: "other" })], legacy).queued,
  ).toEqual([]);
});

it("orders retained plans by their first joined evidence, even when requests arrive last", () => {
  const base = [
    source[0],
    planUpdate({ queued_task_id: "queue-test" }),
    planUpdate({
      plan_id: "earlier-request",
      queued_task_id: "other",
    }),
    { ...lead, payload: { ...lead.payload, requested_plan_id: "earlier-request" } },
    lead,
  ].filter((event) => event !== undefined);
  const result = retained([planUpdate({ revision: 2, queued_task_id: "queue-test" })], base);
  expect(result.queued.map((event) => event.payload.plan_id)).toEqual([
    "earlier-request",
    orderPlan.plan_id,
  ]);
  expect(result.queued.map((event) => event.firstTs)).toEqual([lead.ts, lead.ts]);
});
