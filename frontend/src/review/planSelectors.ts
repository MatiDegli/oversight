import type { Plan, PlanSubtask, QueuedTask } from "@contracts";
import type { State } from "../state";
import type { PlanAttempt } from "./DecisionContext";

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
  const requested = [...plans.values()]
    .flatMap((event) => {
      const plan = event.payload;
      const history = leads.filter((lead) => lead.payload.agent_id === plan.started_by);
      const explicit = history.some((lead) => lead.payload.requested_plan_id);
      const request = explicit
        ? history.find((lead) => lead.payload.requested_plan_id === plan.plan_id)
        : history[0];
      if (!request) return [];
      if (!explicit) {
        // Legacy leads name no plan. Proposed decisions use the lifecycle's unique-proposal rule;
        // later states need a unique sibling too, rather than attributing every terminal plan.
        const siblings = [...plans.values()].filter(
          ({ payload }) =>
            payload.started_by === plan.started_by &&
            (plan.state !== "proposed" || payload.state === "proposed"),
        );
        if (siblings.length !== 1) return [];
      }
      const firstPlan = state.decisionEvents.find(
        (item) =>
          item.type === "plan.updated" &&
          item.payload.plan_id === plan.plan_id &&
          item.payload.started_by === plan.started_by,
      );
      const first = firstPlan && firstPlan.seq > request.seq ? firstPlan : request;
      return [{ ...event, firstSeq: first.seq, firstTs: first.ts }];
    })
    .sort((a, b) => a.firstSeq - b.firstSeq);

  return {
    queued: requested.filter(
      ({ payload }) =>
        (payload.state === "proposed" || payload.state === "starting") && payload.queued_task_id,
    ),
    running: requested.filter(({ payload }) => payload.state === "running"),
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

/** An accepted HTTP response does not settle a command: only its resulting evidence does. */
export function planAttemptPending(
  state: State,
  plan: Plan,
  queue: QueuedTask | undefined,
  attempt?: PlanAttempt,
) {
  if (attempt?.run !== state.run || attempt.planId !== plan.plan_id || attempt.phase !== "pending")
    return false;
  if (attempt.action === "cancel") {
    const cancelled =
      queue ??
      (attempt.queueId ? selectPlanEvidence(state).queue.get(attempt.queueId)?.payload : undefined);
    return (
      !!plan.queued_task_id ||
      cancelled?.state !== "cancelled" ||
      cancelled.revision <= (attempt.queueRevision ?? 0)
    );
  }
  if (plan.revision <= attempt.revision) return true;
  // New plan evidence supersedes Queue even without a link; retry needs another person action.
  return attempt.action === "reject" && plan.state !== "rejected";
}
