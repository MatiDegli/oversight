import type { Plan, QueuedTask, ServerEvent, SuiteExecution } from "@contracts";
import { lazy, Suspense, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import { applyEvent, createInitialState, stateStore, type State } from "../state";
import { DecisionContext, loadDecisionInbox, loadPlanActions, selectPlanEvidence } from "../review";
// eslint-disable-next-line no-restricted-imports -- Test drives the app's shared command context.
import type { PlanAttempt } from "../review/DecisionContext";
import { Frame } from "./Frame";
import { InitialShellLayout } from "./shell/useShell";
import {
  conversationTab,
  navigateView,
  openReview,
  surfaceTab,
  tabbedView,
  viewReducer,
} from "./view";
import { event, seed, status, proposal, resolved, required } from "./chatAgents.fixture";

export type FlowScene =
  "proposed" | "running" | "gates" | "failed" | "inbox" | "large" | "phone" | "long";
export type FlowLayout = "Chat" | "Oversight" | "copies";
const tasks = [
  "Reject negative prices in Item",
  "Reject negative quantities in Cart",
  "Validate reserve quantities in Inventory",
  "Add the checks’ tests to the checkout flow",
  "Reject a negative extra_percent in compute_price",
  "Release reserved lines when place_order fails",
  "Report rejected inputs in the order summary",
  "Cover unknown sku and retry boundaries",
];
const dependencies = [[], [], [0], [0, 1], [1], [2, 4], [2], [3]];
const initialCommit = required(seed.find((item) => item.type === "run.started")).payload
  .base_commit;
export function flowPlan(large = false): Plan {
  return {
    plan_id: "plan-checkout",
    revision: 1,
    task: "Input checks across the checkout flow",
    graph_version: "synthetic",
    state: "proposed",
    started_by: "lead-flow",
    queued_task_id: null,
    detail: null,
    subtasks: tasks.slice(0, large ? 8 : 4).map((task, i) => ({
      task,
      depends_on: dependencies[i] ?? [],
      zone: { node_ids: [], paths: [] },
      state: "waiting",
      agent_id: null,
    })),
  };
}
function testEvent(id: string, phase: SuiteExecution["phase"], failed = false) {
  const source = proposal(id);
  if (source.type !== "change.proposed") throw new Error("Missing proposal");
  return event("tests.executed", {
    agent_id: id,
    change_id: `change-${id}`,
    phase,
    revision: 1,
    tested_commit: phase === "proposal" ? source.payload.head_commit : "synthetic-combined",
    tested_tree: `tree-${id}`,
    suite: {
      ...source.payload.tests,
      outcome: failed ? "failed" : "passed",
      passed: 31,
      failed: failed ? 2 : 0,
    },
  }) as Extract<ServerEvent, { type: "tests.executed" }>;
}
function assigned(plan: Plan, states: ("waiting" | "running" | "merged")[]): Plan {
  return {
    ...plan,
    revision: plan.revision + 1,
    state: "running",
    queued_task_id: "queue-flow",
    subtasks:
      plan.subtasks?.map((task, i) => ({
        ...task,
        state: states[i] ?? "waiting",
        agent_id: states[i] === "waiting" || !states[i] ? null : `flow-${String(i + 1)}`,
      })) ?? null,
  };
}
export function flowEvents(scene: FlowScene): ServerEvent[] {
  const plan = flowPlan(scene === "large");
  if (scene === "long" && plan.subtasks?.[0])
    plan.subtasks[0].task =
      "Validate the extraordinarily long checkout_input_validation_with_inventory_reservations_and_repeated_quantities identifier, preserve every recorded boundary, and cover accented input: acción, 数量, 🛒.";
  const opening = openingEvents(plan);
  if (["proposed", "large", "long"].includes(scene)) return opening;
  const running = assigned(plan, ["running", "running", "waiting", "waiting"]);
  const start = (id: string, slot: "A" | "B", step: number, base_commit = initialCommit) =>
    status(id, {
      slot,
      plan_id: plan.plan_id,
      task: tasks[Number(id.split("-")[1]) - 1] ?? "Checkout checks",
      queued_task_id: "queue-flow",
      base_commit,
      step,
    });
  const events: ServerEvent[] = [
    ...opening,
    event("queue.updated", {
      queued_task_id: "queue-flow",
      revision: 1,
      state: "running",
      plan_id: plan.plan_id,
      plan_revision: 1,
      agent: null,
      agent_ids: ["flow-1", "flow-2"],
      detail: null,
      started_by: null,
    }),
    event("plan.updated", running),
    start("flow-1", "A", 3),
    start("flow-2", "B", 2),
  ];
  if (["running", "inbox"].includes(scene)) return events;
  const first = resolved("flow-1");
  if (first.type !== "change.resolved") throw new Error("Missing resolution");
  events.push(
    testEvent("flow-1", "proposal"),
    proposal("flow-1"),
    {
      ...first,
      payload: { ...first.payload, merge_commit: "synthetic-merged-1", decided_by: "person" },
    },
    status("flow-1", { plan_id: plan.plan_id, state: "done", base_commit: initialCommit }),
    status("flow-2", {
      plan_id: plan.plan_id,
      state: "ready_for_review",
      base_commit: initialCommit,
      slot: "B",
      step: 9,
    }),
    testEvent("flow-2", "proposal"),
    proposal("flow-2"),
    event(
      "plan.updated",
      assigned({ ...running, revision: 2 }, ["merged", "running", "running", "waiting"]),
    ),
    start("flow-3", "A", 5, "synthetic-merged-1"),
  );
  if (scene !== "failed") return events;
  return [...events, ...failedEvents()];
}

function failedEvents(): ServerEvent[] {
  const conflict = resolved("flow-3", true);
  if (conflict.type !== "change.resolved") throw new Error("Missing conflict");
  return [
    testEvent("flow-3", "proposal"),
    proposal("flow-3"),
    testEvent("flow-3", "combined", true),
    {
      ...conflict,
      payload: {
        ...conflict.payload,
        decided_by: "person",
        conflict: {
          kind: "tests",
          paths: [],
          tests: testEvent("flow-3", "combined", true).payload.suite,
        },
      },
    },
  ];
}

const Inbox = lazy(() => loadDecisionInbox().then((module) => ({ default: module.DecisionInbox })));
const PlanActions = lazy(loadPlanActions);
let root: Root | undefined;
let current = createInitialState();
let chatState: State | undefined;
let options = { scene: "proposed" as FlowScene, layout: "Chat" as FlowLayout, readonly: false };
export const sent: { action: string; revision: number; comment?: string }[] = [];
function Harness({ state }: { state: State }) {
  const [attempts, setAttempts] = useState<Record<string, PlanAttempt>>({});
  const [view, setView] = useState(() =>
    viewReducer(tabbedView, {
      type: "open",
      tab:
        options.scene === "inbox" && options.layout === "Oversight"
          ? surfaceTab(tabbedView, "inbox")
          : conversationTab(tabbedView, state, "lead-flow"),
    }),
  );
  const actions = {
    attempts,
    send: (
      plan: Plan,
      action: "queue" | "reject" | "cancel",
      queue?: QueuedTask,
      comment?: string,
    ) => {
      sent.push({ action, revision: plan.revision, comment });
      setAttempts({
        [plan.plan_id]: {
          run: state.run,
          planId: plan.plan_id,
          action,
          revision: plan.revision,
          queueId: queue?.queued_task_id,
          queueRevision: queue?.revision,
          phase: "pending",
        },
      });
    },
  };
  const review = (id: string) => {
    setView((value) => navigateView(value, openReview(state, value, id) ?? value, state));
  };
  return (
    <DecisionContext
      value={{
        planActions: options.readonly ? undefined : actions,
        replayOf: options.readonly ? "synthetic history prefix" : undefined,
      }}
    >
      {options.layout === "copies" ? (
        <div>
          <Suspense fallback={<p>Loading two copies…</p>}>
            <PlanCopies state={state} onReview={review} />
          </Suspense>
        </div>
      ) : (
        <InitialShellLayout value={options.layout}>
          <Frame
            state={state}
            chatState={chatState}
            view={view}
            onReview={review}
            onOpenChange={review}
            canReview={() => true}
            canOpenChange={() => true}
            onNodeTap={() => undefined}
            onClosePanel={() => undefined}
            onTabAction={(action) => {
              setView((value) => viewReducer(value, action));
            }}
            onMessageLead={() => Promise.resolve()}
            onStopLead={() => Promise.resolve()}
            onOpenQueue={() => {
              setView((value) =>
                viewReducer(value, { type: "open", tab: surfaceTab(value, "inbox") }),
              );
            }}
            replayLabel="Synthetic plan evidence · scripted events"
          />
        </InitialShellLayout>
      )}
    </DecisionContext>
  );
}
function render() {
  stateStore.setState({ state: current });
  flushSync(() => root?.render(<Harness state={current} />));
}
export function mountPlanFlow(
  scene: FlowScene = "proposed",
  layout: FlowLayout = "Chat",
  readonly = false,
) {
  options = { scene, layout, readonly };
  chatState = undefined;
  const events = flowEvents(scene).map((item, i) => ({
    ...item,
    seq: i + 1,
    ts: `2026-11-03T19:${String(i).padStart(2, "0")}:00Z`,
  }));
  current = events.reduce(applyEvent, createInitialState());
  sent.length = 0;
  root?.unmount();
  const host = document.createElement("div");
  host.style.height = "100vh";
  document.body.replaceChildren(host);
  root = createRoot(host);
  render();
}
export function holdPlanFlowPrefix() {
  chatState = current;
  options.readonly = true;
  render();
}
export function emitPlanFlow(events: ServerEvent[]) {
  for (const item of events)
    current = applyEvent(current, {
      ...item,
      seq: current.lastSeq + 1,
      ts: "2026-11-03T19:30:00Z",
    });
  render();
}
export function dispatchPlanFlow() {
  const plan = required(selectPlanEvidence(current).plans.get("plan-checkout")).payload;
  emitPlanFlow([
    event("plan.updated", assigned(plan, ["running", "running", "waiting", "waiting"])),
  ]);
}
export function assignBeforeStatus() {
  const plan = required(selectPlanEvidence(current).plans.get("plan-checkout")).payload;
  emitPlanFlow([
    event("plan.updated", {
      ...plan,
      revision: plan.revision + 1,
      subtasks:
        plan.subtasks?.map((task, index) =>
          index === 2 ? { ...task, agent_id: "awaiting-status", state: "running" } : task,
        ) ?? null,
    }),
  ]);
}
export function queuePlanFlow(cancel = false) {
  const plan = required(selectPlanEvidence(current).plans.get("plan-checkout")).payload;
  emitPlanFlow([
    event("queue.updated", {
      queued_task_id: "queue-flow",
      revision: cancel ? 2 : 1,
      state: cancel ? "cancelled" : "queued",
      plan_id: plan.plan_id,
      plan_revision: plan.revision,
      agent: null,
      agent_ids: [],
      detail: null,
      started_by: null,
    }),
    event("plan.updated", {
      ...plan,
      revision: plan.revision + 1,
      queued_task_id: cancel ? null : "queue-flow",
    }),
  ]);
}

function openingEvents(plan: Plan): ServerEvent[] {
  return [
    ...seed,
    event("lead.status", {
      agent_id: "lead-flow",
      revision: 1,
      task: "Add input checks to the shop: reject negative prices and quantities, and zero or negative reserve quantities.",
      model: "muse-spark",
      state: "idle",
      step: 8,
      detail: null,
      requested_plan_id: plan.plan_id,
      limits: null,
    }),
    event("lead.reply", {
      agent_id: "lead-flow",
      text: "I drafted a focused plan. Queue it when you want it to start.",
      answers_seq: null,
      woken_by: null,
      through_seq: 2,
      cut: null,
    }),
    event("plan.updated", plan),
  ];
}

function PlanCopies({ state, onReview }: { state: State; onReview: (id: string) => void }) {
  return (
    <>
      <PlanActions
        state={state}
        plan={required(selectPlanEvidence(state).plans.get("plan-checkout")).payload}
        slots={{ held: 0, total: 2 }}
      />
      <Inbox state={state} onReview={onReview} />
    </>
  );
}
