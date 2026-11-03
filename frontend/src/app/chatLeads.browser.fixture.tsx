import type { AgentStatus, LeadStatus, Plan, ServerEvent, Slot, UsageTick } from "@contracts";
import { useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import { useStore } from "zustand";
import { stateStore } from "../state";
import { createCommandClient, parseEvent } from "../transport";
// eslint-disable-next-line no-restricted-imports -- Fixture resets only the drafts owned by this front.
import { leadDraftStore } from "../agents/leadDraftModel";
// eslint-disable-next-line no-restricted-imports -- Same persistent TaskBox used by production.
import { taskBoxStore } from "../agents/taskBoxModel";
import { Frame } from "./Frame";
import { App } from "./App";
import { InitialShellLayout } from "./shell/useShell";
import { closePanel, conversationTab, tabbedView, viewReducer, surfaceTab } from "./view";
import {
  fold,
  plan as oldPlan,
  seed,
  status as oldStatus,
  required,
  resolved as oldResolved,
} from "./chatAgents.fixture";

const timestamp = "2026-11-02T18:00:00.000Z";
function event<T extends ServerEvent["type"]>(
  type: T,
  payload: Extract<ServerEvent, { type: T }>["payload"],
) {
  return { type, payload, seq: 0, ts: timestamp } as ServerEvent;
}
function status(id: string, overrides: Partial<AgentStatus> = {}) {
  return {
    ...oldStatus(id, { change_id: `change-${id.split("-")[1] ?? "1"}`, ...overrides }),
    ts: timestamp,
  };
}
function plan(id: string, owner: string) {
  const source = oldPlan(id, owner);
  if (source.type !== "plan.updated") throw new Error("Missing plan");
  return {
    ...source,
    ts: timestamp,
    payload: {
      ...source.payload,
      subtasks: ["agent-3", "agent-4"].map((agent_id) => ({
        task: "Subtask",
        zone: { node_ids: [], paths: [] },
        depends_on: [],
        agent_id,
        state: "running" as const,
      })),
    },
  };
}

export function lead(id = "agent-1", overrides: Partial<LeadStatus> = {}) {
  return event("lead.status", {
    agent_id: id,
    revision: 1,
    task: "Make the checkout safer: validate prices and quantities, and cover the edge cases.",
    model: "muse-spark",
    state: "idle",
    step: 10,
    detail: "Requested plan-3.",
    limits: null,
    requested_plan_id: null,
    ...overrides,
  });
}
export const limits = {
  turns: 50,
  plans: 3,
  tool_calls: 12,
  model_calls: 18,
  wall_clock_s: 300,
  subtasks: 6,
  task_chars: 2000,
};
const runSeed = seed.map((item) =>
  item.type === "run.started"
    ? {
        ...item,
        payload: {
          ...item.payload,
          contract_version: "1.16.0",
          merge_mode: { mode: "ask_me" as const, revision: 0 },
        },
      }
    : item,
);
export function leadScenario(name: string): ServerEvent[] {
  if (name === "same-model")
    return runSeed.map((item) =>
      item.type === "run.started"
        ? {
            ...item,
            payload: {
              ...item.payload,
              slots: item.payload.slots.map((slot) => ({ ...slot, default_model: "muse-spark" })),
            },
          }
        : item,
    );
  if (name === "chat-start" || name === "lead-new") return runSeed;
  if (name === "task-direct")
    return [...runSeed, status("agent-5", { task: "Reject negative prices and quantities." })];
  if (!["leads-two", "lead-switch"].includes(name)) throw new Error(`Unknown lead scene: ${name}`);
  return [
    ...runSeed,
    lead(),
    lead("agent-2", {
      task: "Find the inventory edge cases and propose a focused test plan.",
      state: "working",
      step: 4,
      detail: "Reading inventory.py",
    }),
    plan("plan-1", "agent-1"),
    status("agent-3", { plan_id: "plan-1", task: "Reject negative unit prices.", slot: "A" }),
    status("agent-4", {
      plan_id: "plan-1",
      task: "Validate quantities.",
      state: "ready_for_review",
      slot: "B",
    }),
    status("agent-5", { state: "done", task: "Document checkout validation." }),
    event("lead.reply", {
      agent_id: "agent-1",
      answers_seq: null,
      woken_by: null,
      text: "I split validation and its edge-case tests into a focused plan. You can queue it after review.",
      through_seq: 3,
      cut: null,
    }),
  ];
}
const goalA =
  "Add input checks to the shop: reject negative prices and quantities, and zero or negative reserve quantities.";
const goalB =
  "Make a failed order leave no trace: place_order and cancel_order keep the stock as it was.";
const rounding = "Round prices to two decimals in Item.total";
function approvedRun(teams: boolean) {
  return runSeed.map((item) =>
    item.type === "run.started"
      ? {
          ...item,
          payload: {
            ...item.payload,
            models: item.payload.models.map((model) => ({ ...model, reasoning: "low" })),
            slots: (teams ? (["A", "B", "C", "D"] as Slot[]) : (["A", "B"] as Slot[])).map(
              (slot) => ({ slot, default_model: slot === "B" ? "muse-spark-open" : "muse-spark" }),
            ),
            budget: { ...item.payload.budget, budget_usd: 150 },
          },
        }
      : item,
  );
}
function tool(id: string, step: number, summary: string, name = "edit_file") {
  return event("agent.step", {
    agent_id: id,
    step,
    tool: name,
    summary,
    result: "ok",
    duration_ms: 1,
    error: null,
    error_kind: null,
    tests: null,
  });
}
function merged(id: string) {
  const source = oldResolved(id);
  if (source.type !== "change.resolved") throw new Error("Missing merge");
  return {
    ...source,
    ts: timestamp,
    payload: {
      ...source.payload,
      change_id: `change-${id.split("-")[1] ?? "1"}`,
      decided_by: "person" as const,
    },
  };
}
function approvedPlan(
  id: string,
  owner: string,
  task: string,
  proposed: boolean,
  agents: string[] = [],
) {
  const subtasks: NonNullable<Plan["subtasks"]> = proposed
    ? [
        {
          task: "Release the reserved lines in place_order when a later line fails, with a test in tests/test_orders.py.",
          zone: { paths: ["shop/orders.py"], node_ids: [] },
          depends_on: [],
          agent_id: null,
          state: null,
        },
        {
          task: "Add a test that cancel_order after a failed place_order leaves the stock unchanged.",
          zone: { paths: ["tests/test_orders.py"], node_ids: [] },
          depends_on: [0],
          agent_id: null,
          state: null,
        },
      ]
    : agents.map((agent_id, i) => ({
        task: `Validation task ${String(i + 1)}`,
        zone: { paths: ["shop/pricing.py"], node_ids: [] },
        depends_on: [],
        agent_id,
        state: i === 0 ? "merged" : "running",
      }));
  return event("plan.updated", {
    plan_id: id,
    started_by: owner,
    task,
    revision: 1,
    graph_version: "fixture",
    state: proposed ? "proposed" : "running",
    subtasks,
    detail: null,
    queued_task_id: null,
  });
}
function costs(entries: [string, number, UsageTick["role"]][]) {
  let spent = 0;
  return entries.map(([agent_id, cost_usd, role]) => {
    spent += cost_usd;
    return event("usage.tick", {
      agent_id,
      model: "muse-spark",
      input_tokens: 10,
      output_tokens: 10,
      cached_tokens: null,
      role,
      change_id: null,
      plan_id: null,
      cost_usd,
      spent_usd: spent,
      budget_usd: 150,
      brake: "ok",
    });
  });
}
function approvedDirect() {
  return [
    ...approvedRun(false),
    status("agent-1", {
      task: "compute_price in shop/pricing.py accepts a negative unit_price or quantity and returns a negative price.",
      state: "done",
      step: 7,
    }),
    merged("agent-1"),
    status("agent-2", {
      task: "Reject zero and negative reserve quantities.",
      slot: "B",
      model: "muse-spark-open",
      state: "done",
      step: 6,
    }),
    merged("agent-2"),
    status("agent-3", { task: rounding, state: "working", step: 6 }),
    tool("agent-3", 1, "Listed 14 files", "list_files"),
    tool("agent-3", 2, "Read models.py", "read_file"),
    tool("agent-3", 3, "Searched Item.total: 3 matches", "search"),
    tool("agent-3", 4, "Read test_models.py", "read_file"),
    event("agent.message", {
      agent_id: "agent-3",
      text: "Round half up, the way format_cents does.",
    }),
    tool("agent-3", 5, "Edited Item.total in models.py, lines 31 to 38"),
    tool("agent-3", 6, "Edited test_models.py"),
    ...costs([
      ["agent-1", 0.03, "agent"],
      ["agent-2", 0.02, "agent"],
      ["agent-3", 0.01, "agent"],
    ]),
  ];
}
function approvedTeams(name: string) {
  const fresh = name === "lead-new";
  const agents = fresh
    ? ["agent-3", "agent-4", "agent-5", "agent-7"]
    : ["agent-3", "agent-4", "agent-5"];
  const prefix = [
    ...approvedRun(true),
    lead("agent-1", {
      task: goalA,
      state: fresh ? "stopped" : "working",
      limits,
      requested_plan_id: "plan-3",
      step: name === "lead-switch" ? 11 : 10,
      detail: fresh ? "Stopped by the person." : "Requested plan-3.",
    }),
    lead("agent-2", {
      task: goalB,
      model: "muse-spark-open",
      state: fresh ? "working" : "idle",
      limits,
      step: fresh ? 9 : 6,
      requested_plan_id: "plan-4",
      detail: fresh ? "Read orders.py" : "Requested plan-4.",
    }),
    approvedPlan("plan-3", "agent-1", goalA, false, agents),
    status("agent-3", {
      plan_id: "plan-3",
      task: "Reject negative prices in Item",
      state: "done",
      step: 6,
    }),
    merged("agent-3"),
    status("agent-4", {
      plan_id: "plan-3",
      task: "Reject negative quantities in Cart.add",
      slot: "B",
      state: "ready_for_review",
      step: 7,
    }),
    status("agent-5", {
      plan_id: "plan-3",
      task: "Validate reserve quantities in Inventory.reserve",
      slot: "C",
      state: fresh ? "ready_for_review" : "working",
      step: 5,
    }),
    tool("agent-5", 5, "Edited reserve in inventory.py"),
    ...(fresh
      ? [
          status("agent-7", {
            plan_id: "plan-3",
            task: "Cover reserve edge cases in tests/test_inventory.py",
            state: "done",
            step: 5,
          }),
          merged("agent-7"),
          approvedPlan("plan-4", "agent-2", goalB, false, ["agent-8"]),
          status("agent-8", {
            plan_id: "plan-4",
            task: "Release the reserved lines in place_order",
            state: "working",
            step: 4,
          }),
          tool("agent-8", 4, "Edited place_order in orders.py"),
        ]
      : [
          approvedPlan("plan-4", "agent-2", goalB, true),
          status("agent-6", { task: rounding, slot: "D", step: 6 }),
          tool("agent-6", 6, "Edited test_models.py"),
        ]),
    event("lead.reply", {
      agent_id:
        name === "leads-six"
          ? "lead-d"
          : name === "task-direct"
            ? "agent-3"
            : name === "lead-switch"
              ? "agent-2"
              : "agent-1",
      answers_seq: null,
      woken_by: null,
      text:
        name === "lead-switch"
          ? "I drafted plan-4, Roll back failed orders. #1 starts first; #2 waits for it. Queue it when you want to start."
          : "I drafted plan-3 with four subtasks. #1 and #2 start together; #3 waits for #1, and #4 waits for #2. Queue it when you want to start.",
      through_seq: null,
      cut: null,
    }),
    ...costs([
      ["agent-3", 0.03, "agent"],
      ["agent-4", 0.03, "agent"],
      ["agent-5", 0.02, "agent"],
      [fresh ? "agent-8" : "agent-6", 0.01, "agent"],
      ["agent-1", 0.02, "lead"],
      ["agent-2", 0.01, "lead"],
    ]),
  ];
  return prefix;
}
function lifecycleScene(name: string) {
  const six = name === "leads-six";
  const base = approvedTeams("lead-new");
  const extra = six
    ? [
        lead("lead-c", {
          task: "Give premium customers free shipping and cover the edge cases.",
          state: "working",
          limits,
          step: 4,
          detail: "Reading pricing.py",
        }),
        lead("lead-d", {
          task: "Lowercase customer emails everywhere and reject bad addresses in every setter.",
          state: "starting",
          limits,
          step: 0,
          detail: null,
        }),
        lead("lead-e", {
          task: "Speed up revenue by category for large order histories.",
          state: "idle",
          limits,
          detail: null,
        }),
        lead("lead-f", {
          task: "Make reorder_quote raise for an unknown product.",
          state: "idle",
          limits,
          detail: null,
        }),
      ]
    : [];
  const events: ServerEvent[] = [];
  for (const item of base) {
    if (
      six &&
      (item.type === "agent.status" ||
        item.type === "agent.step" ||
        item.type === "change.resolved") &&
      ["agent-3", "agent-5", "agent-7"].includes(item.payload.agent_id)
    )
      continue;

    events.push(item);
    if (item.type === "lead.status" && item.payload.agent_id === "agent-2") events.push(...extra);
  }
  if (six) {
    events.push(
      approvedPlan("plan-6", "lead-d", "Normalize and validate customer email setters.", false, [
        "agent-9",
      ]),
      status("agent-9", {
        plan_id: "plan-6",
        task: "Reject an address without an at sign.",
        state: "ready_for_review",
        slot: "C",
        step: 9,
      }),
      approvedPlan("plan-8", "lead-f", "Reject unknown products in reorder_quote.", true),
    );
    for (let turn = 1; turn <= limits.turns; turn++) {
      events.push(
        lead("lead-d", {
          task: "Lowercase customer emails everywhere and reject bad addresses in every setter.",
          revision: turn * 2,
          state: "working",
          limits,
          step: Math.floor(((turn - 1) * 186) / limits.turns),
          detail: null,
        }),
        event("lead.reply", {
          agent_id: "lead-d",
          answers_seq: null,
          woken_by: null,
          text:
            turn === limits.turns
              ? "No. Guest checkout builds a Customer through the same email setter, so the existing tests cover it."
              : `Checked email validation, turn ${String(turn)}.`,
          through_seq: null,
          cut: null,
        }),
        lead("lead-d", {
          task: "Lowercase customer emails everywhere and reject bad addresses in every setter.",
          revision: turn * 2 + 1,
          state: turn === limits.turns ? "halted" : "idle",
          limits,
          step: Math.floor((turn * 186) / limits.turns),
          detail:
            turn === limits.turns ? `Reached the limit of ${String(limits.turns)} turns.` : null,
        }),
      );
    }
    events.push(...costs([["lead-d", 0.21, "lead"]]));
  }
  return events;
}
export function approvedScenario(name: string) {
  if (name === "lead-stopped" || name === "leads-six") return lifecycleScene(name);
  if (name === "lead-cap")
    return approvedTeams("leads-two").map((item) =>
      item.type === "run.started" ? { ...item, payload: { ...item.payload, max_leads: 2 } } : item,
    );
  if (
    !["chat-start", "start-phone", "task-direct", "leads-two", "lead-switch", "lead-new"].includes(
      name,
    )
  )
    throw new Error(`Unknown approved scene: ${name}`);
  return name === "chat-start" || name === "start-phone"
    ? approvedRun(false)
    : name === "task-direct"
      ? approvedDirect()
      : approvedTeams(name);
}
let root: Root | undefined;
const commands = createCommandClient();
function Harness({ name, live, approved }: { name: string; live: boolean; approved: boolean }) {
  const state = useStore(stateStore, (store) => store.state);
  const [view, setView] = useState(() =>
    ["leads-two", "lead-switch", "lead-stopped", "leads-six", "lead-cap"].includes(name) ||
    (approved && name === "task-direct")
      ? viewReducer(tabbedView, {
          type: "open",
          tab: conversationTab(
            tabbedView,
            state,
            name === "leads-six"
              ? "lead-d"
              : name === "task-direct"
                ? "agent-3"
                : name === "lead-switch"
                  ? "agent-2"
                  : "agent-1",
          ),
        })
      : tabbedView,
  );
  return (
    <Frame
      state={state}
      view={view}
      onNodeTap={() => undefined}
      canOpenChange={() => false}
      onOpenChange={() => undefined}
      onClosePanel={() => {
        setView(closePanel);
      }}
      onTabAction={(action) => {
        setView((current) => viewReducer(current, action));
      }}
      onOpenConversation={(id) => {
        setView((current) =>
          viewReducer(current, { type: "open", tab: conversationTab(current, state, id) }),
        );
      }}
      onOpenSettings={() => {
        setView((current) =>
          viewReducer(current, { type: "open", tab: surfaceTab(current, "settings") }),
        );
      }}
      onOpenQueue={() => {
        setView((current) =>
          viewReducer(current, { type: "open", tab: surfaceTab(current, "inbox") }),
        );
      }}
      onStartAgent={live ? commands.startAgent : undefined}
      onStartLead={live ? commands.startLead : undefined}
      onStopLead={live ? commands.stopLead : undefined}
      onMessageAgent={live ? (id, text) => commands.messageAgent(id, { text }) : undefined}
      onPauseAgent={live ? commands.pauseAgent : undefined}
      onStopAgent={live ? commands.stopAgent : undefined}
      onMessageLead={live ? (id, text) => commands.messageLead(id, { text }) : undefined}
      replayLabel="Handwritten K2a fixture"
    />
  );
}
export const chatLeadsTest = {
  mount(name = "chat-start", live = true, app = false, approved = false) {
    root?.unmount();
    taskBoxStore.setState({ draft: "", pending: false, failure: null, choice: null });
    leadDraftStore.setState({ draft: "", pending: false, failure: null });
    stateStore.setState({
      state: fold(
        (approved ? approvedScenario(name) : leadScenario(name)).map((item, i) =>
          parseEvent(JSON.stringify({ ...item, seq: i + 1 })),
        ),
      ),
    });
    root = createRoot(required(document.getElementById("root")));
    flushSync(() =>
      root?.render(
        <InitialShellLayout value="Chat">
          {app ? <App /> : <Harness name={name} live={live} approved={approved} />}
        </InitialShellLayout>,
      ),
    );
  },
  deliver(events: ServerEvent[]) {
    let seq = stateStore.getState().state.lastSeq;
    flushSync(() => {
      stateStore
        .getState()
        .applyEvents(events.map((item) => parseEvent(JSON.stringify({ ...item, seq: ++seq }))));
    });
  },
  work: () => {
    const { agents, changes } = stateStore.getState().state;
    return { agents, changes };
  },
  events: () => stateStore.getState().state.decisionEvents,
  prefix: (name: string, approved = false) =>
    (approved ? approvedScenario(name) : leadScenario(name)).map((item, i) => ({
      ...item,
      seq: i + 1,
    })),
};
Object.assign(window, { chatLeadsTest });
declare global {
  interface Window {
    chatLeadsTest: typeof chatLeadsTest;
  }
}
