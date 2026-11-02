import type { AgentStatus, LeadStatus, ServerEvent } from "@contracts";
import { expect, it } from "vitest";
// eslint-disable-next-line no-restricted-imports -- Shared event builders exercise the production reducer.
import {
  fold,
  plan as oldPlan,
  resolved as oldResolved,
  seed,
  status as oldStatus,
} from "../app/chatAgents.fixture";
import { selectChatLeads, selectChatRows, selectLeadLimits } from "./chatLeadSelectors";
import { holderReason } from "./taskBoxModel";

const ts = "2026-11-02T18:00:00.000Z";
function event<T extends ServerEvent["type"]>(
  type: T,
  payload: Extract<ServerEvent, { type: T }>["payload"],
) {
  return { type, payload, seq: 0, ts } as ServerEvent;
}
function status(id: string, overrides: Partial<AgentStatus> = {}) {
  return { ...oldStatus(id, { change_id: `change-${id.split("-")[1] ?? "1"}`, ...overrides }), ts };
}
function plan(id: string, owner: string, revision = 1) {
  return { ...oldPlan(id, owner, revision), ts };
}
function resolved(id: string) {
  const item = oldResolved(id);
  if (item.type !== "change.resolved") throw new Error("Missing resolution");
  return {
    ...item,
    ts,
    payload: { ...item.payload, change_id: `change-${id.split("-")[1] ?? "1"}` },
  };
}
function lead(id = "agent-1", overrides: Partial<LeadStatus> = {}) {
  return event("lead.status", {
    agent_id: id,
    revision: 1,
    task: "Improve validation",
    model: "muse-spark",
    state: "working",
    step: 10,
    detail: "Requested plan-3.",
    requested_plan_id: null,
    limits: null,
    ...overrides,
  });
}
it("opens a lead team before its first member, interleaves direct work and retains member order", () => {
  const state = fold([
    ...seed,
    lead(),
    status("agent-3"),
    plan("plan-1", "agent-1"),
    status("agent-4", { plan_id: "plan-1" }),
    lead("agent-2"),
    status("agent-5"),
  ]);
  expect(selectChatRows(state).map((row) => [row.id, row.letter])).toEqual([
    ["agent-1", "A"],
    ["agent-4", "A·1"],
    ["agent-3", "B"],
    ["agent-2", "C"],
    ["agent-5", "D"],
  ]);
  expect(selectChatLeads(state)[0]?.activity).toBe("Requested plan-3. · Step 10");
  const member = state.agents["agent-4"];
  if (!member) throw new Error("Missing member");
  expect(holderReason(state, member)).toBe("Agent A·1 is working.");
});
it.each(["starting", "working", "idle"] as const)(
  "keeps the lead hue occupied while %s",
  (state) => {
    const prefix = [
      ...seed,
      lead("agent-1", { state }),
      ...Array.from({ length: 5 }, (_, i) => status(`agent-${String(i + 2)}`)),
      resolved("agent-2"),
      status("agent-7"),
    ];
    expect(selectChatRows(fold(prefix)).at(-1)?.hue).toBe("B");
  },
);
it("counts only team members, active coding work, review and latest proposed plans", () => {
  const proposed = plan("plan-1", "agent-1");
  if (proposed.type !== "plan.updated") throw new Error("Missing plan");
  const planEvent = {
    ...proposed,
    payload: {
      ...proposed.payload,
      state: "proposed" as const,
      subtasks: [
        {
          task: "Validate prices",
          zone: { paths: ["shop/pricing.py"], node_ids: [] },
          depends_on: [],
          agent_id: null,
          state: null,
        },
      ],
    },
  };
  const events = [
    ...seed,
    lead(),
    planEvent,
    status("agent-2", { plan_id: "plan-1" }),
    status("agent-3", { plan_id: "plan-1", state: "ready_for_review" }),
    status("agent-4", { plan_id: "plan-1", state: "paused" }),
    status("agent-5"),
  ];
  expect(selectChatLeads(fold(events))[0]?.teamLine).toBe("3 agents · 1 working, 2 need you");
  expect(selectChatLeads(fold([...events, plan("plan-1", "agent-1", 2)]))[0]?.teamLine).toBe(
    "3 agents · 1 working, 1 needs you",
  );
  expect(selectChatLeads(fold([...seed, lead(), planEvent]))[0]?.teamLine).toBe("1 plan needs you");
  expect(selectChatLeads(fold([...seed, lead()]))[0]?.teamLine).toBeNull();
});
it("uses the current status's last non-null detail and ignores stale revisions", () => {
  const events = [...seed, lead(), lead("agent-1", { revision: 2, detail: null })];
  expect(selectChatLeads(fold(events))[0]?.activity).toBe("Requested plan-3. · Step 10");
  const idle = [
    ...events,
    lead("agent-1", { revision: 3, state: "idle", detail: null }),
    lead("agent-1", { revision: 1, state: "halted", detail: "Stale" }),
  ];
  expect(selectChatLeads(fold(idle))[0]?.activity).toBe("Step 10");
  expect(selectLeadLimits(fold(events))).toBeNull();
});
it("retains only observed limits across later nulls, with no guessed values", () => {
  const limits = {
    turns: 50,
    plans: 3,
    tool_calls: 12,
    model_calls: 18,
    wall_clock_s: 300,
    subtasks: 6,
    task_chars: 2000,
  };
  expect(
    selectLeadLimits(
      fold([...seed, lead("agent-1", { limits }), lead("agent-1", { revision: 2 })]),
    ),
  ).toEqual(limits);
});
