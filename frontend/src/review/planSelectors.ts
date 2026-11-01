import type { Plan, PlanSubtask } from "@contracts";
import type { State } from "../state";

type Evidence<T extends "plan.updated" | "queue.updated"> = Extract<
  State["decisionEvents"][number],
  { type: T }
>;

/** Each counter belongs to its own object; keep the winning envelope, including its time. */
export function selectPlanEvidence(state: State) {
  const plans = new Map<string, Evidence<"plan.updated">>();
  const queue = new Map<string, Evidence<"queue.updated">>();
  for (const event of state.decisionEvents) {
    if (event.type === "plan.updated") {
      const id = event.payload.plan_id;
      if ((plans.get(id)?.payload.revision ?? 0) < event.payload.revision) plans.set(id, event);
    }
    if (event.type === "queue.updated") {
      const id = event.payload.queued_task_id;
      if ((queue.get(id)?.payload.revision ?? 0) < event.payload.revision) queue.set(id, event);
    }
  }
  return { plans, queue };
}

export function selectPlanQueue(state: State, plan: Plan) {
  const item = plan.queued_task_id
    ? selectPlanEvidence(state).queue.get(plan.queued_task_id)
    : undefined;
  return item?.payload.plan_id === plan.plan_id ? item : undefined;
}

/** Retained groups do not create decisions. Explicit request history also survives nulls. */
export function selectRetainedPlans(state: State) {
  const { plans } = selectPlanEvidence(state);
  const leads = state.decisionEvents.filter((event) => event.type === "lead.status");
  const requested = [...plans.values()].filter(({ payload: plan }) => {
    const history = leads.filter((event) => event.payload.agent_id === plan.started_by);
    if (history.some((event) => event.payload.requested_plan_id))
      return history.some((event) => event.payload.requested_plan_id === plan.plan_id);
    return (
      history.length > 0 &&
      [...plans.values()].filter(
        (event) =>
          event.payload.started_by === plan.started_by && event.payload.state === "proposed",
      ).length === (plan.state === "proposed" ? 1 : 0)
    );
  });
  return {
    queued: requested.filter(
      ({ payload }) => payload.state === "proposed" && payload.queued_task_id,
    ),
    failed: requested.filter(({ payload }) => payload.state === "failed"),
  };
}

export const firstWaveSize = (plan: Plan) =>
  plan.subtasks?.filter((task) => task.depends_on.length === 0).length ?? 0;
export const planFits = (plan: Plan, offered: number) => firstWaveSize(plan) <= offered;
export function dependencyWords(task: PlanSubtask) {
  const numbers = task.depends_on.map((index) => String(index + 1));
  if (!numbers.length) return "Starts first";
  const last = numbers.pop() ?? "";
  return `Starts after ${numbers.length ? `${numbers.join(", ")} and ${last} merge` : `${last} merges`}`;
}
