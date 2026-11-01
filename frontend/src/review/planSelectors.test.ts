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
  planFits,
  selectPlanEvidence,
  selectPlanQueue,
  selectRetainedPlans,
} from "./planSelectors";

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
