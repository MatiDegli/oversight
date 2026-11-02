import type { Plan, PlanSubtask, QueuedTask, ServerEvent } from "@contracts";
import log from "../../../fixtures/events/recorded-demo.jsonl?raw";
import leadLog from "../../../fixtures/events/handwritten-lead-plans.jsonl?raw";
import { parseEvent } from "../transport";

// Existing run/graph and B-before-A proposals; later sketch events are hand-written test data.
export const leadActionsBase = log
  .trim()
  .split("\n")
  .map(parseEvent)
  .filter((e) => e.seq <= 63);
const lead = leadLog
  .trim()
  .split("\n")
  .map(parseEvent)
  .find((e) => e.type === "lead.status");
const tasks = (path: string, rows: [string, number[]][]): PlanSubtask[] =>
  rows.map(([task, depends_on]) => ({
    task,
    depends_on,
    zone: { node_ids: [], paths: [path] },
    state: "waiting",
    agent_id: null,
  }));
export const cartPlan: Plan = {
  plan_id: "plan-cart",
  revision: 1,
  task: "Reject bad quantities in the cart",
  graph_version: "test-graph",
  state: "proposed",
  started_by: "lead-test",
  queued_task_id: null,
  detail: null,
  subtasks: tasks("shop/cart.py", [
    [
      "Make Cart.add and Cart.remove raise ValueError for a quantity below one, with tests in tests/test_cart.py.",
      [],
    ],
    [
      "Make Cart.quote_add reject the same quantities and cover a tier boundary in tests/test_cart.py.",
      [0],
    ],
  ]),
};
export const orderPlan: Plan = {
  ...cartPlan,
  plan_id: "plan-orders",
  task: "Roll back failed orders",
  subtasks: tasks("shop/orders.py", [
    [
      "Release the reserved lines in place_order when a later line fails, with a test in tests/test_orders.py.",
      [],
    ],
    ["Add a test that cancel_order after a failed place_order leaves the stock unchanged.", [0]],
    [
      "Make reorder_quote raise ValueError for an unknown sku, with a test in tests/test_orders.py.",
      [],
    ],
  ]),
};
export const fourthTask: PlanSubtask = {
  state: "waiting",
  agent_id: null,
  task: "Make revenue_by_category skip cancelled orders, with a test.",
  depends_on: [2],
  zone: { node_ids: [], paths: ["shop/orders.py"] },
};
export const queueItem: QueuedTask = {
  queued_task_id: "queue-orders",
  revision: 7,
  state: "queued",
  plan_id: orderPlan.plan_id,
  plan_revision: 2,
  agent: null,
  agent_ids: [],
  detail: null,
  started_by: null,
};
export function planRequest(plan: Plan, revision: number): ServerEvent[] {
  if (lead?.type !== "lead.status") throw new Error("Missing lead template");
  return [
    {
      ...lead,
      payload: {
        ...lead.payload,
        agent_id: "lead-test",
        revision,
        requested_plan_id: plan.plan_id,
      },
    },
    { seq: 0, ts: "", type: "plan.updated", payload: plan },
  ];
}
