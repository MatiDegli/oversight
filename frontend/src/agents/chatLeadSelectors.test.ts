import type { LeadStatus, ServerEvent } from "@contracts";
import { expect, it } from "vitest";
// eslint-disable-next-line no-restricted-imports -- Shared valid event builders exercise the production reducer.
import { event, fold, plan, resolved, seed, status } from "../app/chatAgents.fixture";
import { selectChatLeads, selectChatRows, selectLeadLimits } from "./chatLeadSelectors";
import { holderReason } from "./taskBoxModel";

function lead(id = "lead-a", overrides: Partial<LeadStatus> = {}) {
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
    status("direct"),
    plan("p", "lead-a"),
    status("member", { plan_id: "p" }),
    lead("lead-b"),
    status("next"),
  ]);
  expect(selectChatRows(state).map((row) => [row.id, row.letter])).toEqual([
    ["lead-a", "A"],
    ["member", "A·1"],
    ["direct", "B"],
    ["lead-b", "C"],
    ["next", "D"],
  ]);
  expect(selectChatLeads(state)[0]?.activity).toBe("Requested plan-3. · Step 10");
  const member = state.agents.member;
  if (!member) throw new Error("Missing member");
  expect(holderReason(state, member)).toBe("Agent A·1 is working.");
});
it.each(["starting", "working", "idle"] as const)(
  "keeps the lead hue occupied while %s",
  (state) => {
    const prefix = [
      ...seed,
      lead("lead-a", { state }),
      ...Array.from({ length: 5 }, (_, i) => status(`direct-${String(i)}`)),
      resolved("direct-0"),
      status("new"),
    ];
    expect(selectChatRows(fold(prefix)).at(-1)?.hue).toBe("B");
  },
);
it("counts only team members, active coding work, review and latest proposed plans", () => {
  const proposed = plan("p", "lead-a");
  if (proposed.type !== "plan.updated") throw new Error("Missing plan");
  const events: ServerEvent[] = [
    ...seed,
    lead(),
    { ...proposed, payload: { ...proposed.payload, state: "proposed" } },
    status("working", { plan_id: "p" }),
    status("ready", { plan_id: "p", state: "ready_for_review" }),
    status("paused", { plan_id: "p", state: "paused" }),
    status("other"),
  ];
  expect(selectChatLeads(fold(events))[0]?.teamLine).toBe("3 agents · 1 working, 2 need you");
  expect(selectChatLeads(fold([...events, plan("p", "lead-a", 2)]))[0]?.teamLine).toBe(
    "3 agents · 1 working, 1 needs you",
  );
  expect(
    selectChatLeads(
      fold([...seed, lead(), { ...proposed, payload: { ...proposed.payload, state: "proposed" } }]),
    )[0]?.teamLine,
  ).toBe("1 plan needs you");
  expect(selectChatLeads(fold([...seed, lead()]))[0]?.teamLine).toBeNull();
});
it("uses the current status's last non-null detail, and never invents a tool summary", () => {
  const events = [...seed, lead(), lead("lead-a", { revision: 2, detail: null })];
  expect(selectChatLeads(fold(events))[0]?.activity).toBe("Requested plan-3. · Step 10");
  expect(
    selectChatLeads(
      fold([...events, lead("lead-a", { revision: 3, state: "idle", detail: null })]),
    )[0]?.activity,
  ).toBe("Step 10");
  expect(selectLeadLimits(fold(events))).toBeNull();
});
