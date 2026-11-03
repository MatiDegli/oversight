import type { LeadStatus, ServerEvent, UsageTick } from "@contracts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import source from "../../../../fixtures/events/handwritten-lead-plans.jsonl?raw";
import { applyEvent, createInitialState } from "../../state";
import { parseEvent } from "../../transport";
import { leadCapacity, newLeadLabel } from "../chatLeadSelectors";
import { selectLeadConversation } from "./selectLeadConversation";
import { ConversationPanelHeader } from "./ConversationPanelHeader";
import LeadThreadRows from "./LeadThreadRows";

function required<T>(value: T | undefined): T {
  if (value === undefined) throw new Error("Missing fixture value");
  return value;
}
const recorded = source.trim().split("\n").map(parseEvent);
const run = required(recorded.find((e) => e.type === "run.started"));
const plan = required(recorded.find((e) => e.type === "plan.updated"));
const status = required(recorded.find((e) => e.type === "lead.status"));
const reply: Extract<ServerEvent, { type: "lead.reply" }> = {
  type: "lead.reply",
  seq: 0,
  ts: run.ts,
  payload: {
    agent_id: "lead-a",
    text: "A completed turn.",
    answers_seq: null,
    woken_by: null,
    through_seq: null,
    cut: null,
  },
};
const limits = {
  turns: 7,
  plans: 5,
  tool_calls: 21,
  model_calls: 32,
  wall_clock_s: 450,
  subtasks: 4,
  task_chars: 12345,
};
const lead = (
  revision: number,
  state: LeadStatus["state"],
  step: number,
  other: Partial<LeadStatus> = {},
): ServerEvent => ({
  ...status,
  payload: { ...status.payload, agent_id: "lead-a", revision, state, step, limits, ...other },
});
const answer = (
  cut: "tool_calls" | "model_calls" | "time" | "spend_brake" | null = null,
): ServerEvent => ({
  ...reply,
  payload: {
    ...reply.payload,
    agent_id: "lead-a",
    answers_seq: null,
    woken_by: null,
    through_seq: null,
    cut,
  },
});
const fold = (events: ServerEvent[], opening: ServerEvent = run) =>
  [opening, ...events]
    .map((e, i) => parseEvent(JSON.stringify({ ...e, seq: i + 1 })))
    .reduce(applyEvent, createInitialState());
const project = (events: ServerEvent[]) => required(selectLeadConversation(fold(events), "lead-a"));
const html = (events: ServerEvent[]) =>
  renderToStaticMarkup(createElement(ConversationPanelHeader, { header: project(events).header }));

it("projects nondefault limits, exact task, profile reasoning and distinct owned plans across revisions", () => {
  const events = [
    lead(1, "working", 100, { task: "Keep THIS exact goal." }),
    { ...plan, payload: { ...plan.payload, plan_id: "one", started_by: "lead-a" } },
    {
      ...plan,
      payload: {
        ...plan.payload,
        plan_id: "one",
        started_by: "lead-a",
        revision: 2,
        state: "rejected" as const,
      },
    },
    { ...plan, payload: { ...plan.payload, plan_id: "two", started_by: "lead-a" } },
    { ...plan, payload: { ...plan.payload, plan_id: "foreign", started_by: "lead-b" } },
  ];
  expect(project(events).header).toMatchObject({
    task: "Keep THIS exact goal.",
    step: 100,
    turnCalls: 100,
    turn: 1,
    plans: 2,
    reasoning: run.payload.models.find((m) => m.id === status.payload.model)?.reasoning ?? null,
  });
  expect(html(events)).toContain(
    "Limits: 7 turns · 21 tool calls per turn · 32 model calls per turn · 450 s per turn · 5 plans · 4 subtasks per plan · 12,345 characters per task.",
  );
  expect(html(events)).toContain("Plans 2 of 5");
  expect(html(events)).not.toContain("Cost ");
  expect(html([lead(1, "idle", 2, { model: "historical-model" })])).toContain("historical-model");
});
it("does not borrow another lead's limits or invent null denominators", () => {
  const events = [
    lead(1, "working", 4, { limits: null }),
    lead(1, "working", 99, { agent_id: "lead-b" }),
  ];
  expect(project(events).header).toMatchObject({ turn: 1, turnCalls: 4, limits: null });
  const markup = html(events);
  expect(markup).toContain("Limits not recorded.");
  expect(markup).toContain("This turn: 4 tool calls");
  expect(markup).not.toContain(" of ");
});
it("uses the completed status after each reply for the next baseline, including the intervening prefix", () => {
  const events = [lead(1, "starting", 0), lead(2, "working", 0), lead(3, "working", 4)];
  expect(project(events).header).toMatchObject({ turn: 1, step: 4, turnCalls: 4 });
  events.push(answer());
  expect(project(events).header).toMatchObject({ turn: 1, step: 4, turnCalls: 4 });
  events.push(lead(4, "idle", 5));
  expect(project(events).header).toMatchObject({ turn: 1, step: 5, turnCalls: 5 });
  events.push(
    lead(5, "working", 5),
    lead(6, "working", 7),
    lead(1, "working", 200, { agent_id: "lead-b" }),
  );
  expect(project(events).header).toMatchObject({ turn: 2, step: 7, turnCalls: 2 });
  events.push(answer(), lead(7, "halted", 8), lead(6, "working", 99));
  expect(project(events).header).toMatchObject({ turn: 2, step: 8, turnCalls: 3, state: "halted" });
});
const tick = (agent_id: string, role: UsageTick["role"], cost_usd: number | null): ServerEvent => ({
  type: "usage.tick",
  seq: 0,
  ts: run.ts,
  payload: {
    agent_id,
    role,
    cost_usd,
    model: "muse-spark",
    input_tokens: 1,
    output_tokens: 1,
    cached_tokens: null,
    change_id: null,
    plan_id: null,
    spent_usd: 99,
    budget_usd: 150,
    brake: "ok",
  },
});
it("accumulates only this lead's role, preserves unknown and resets on a new run", () => {
  const events = [
    lead(1, "idle", 2),
    tick("lead-a", "lead", 0.12),
    tick("lead-a", "agent", 30),
    tick("lead-a", "review", null),
    tick("lead-b", "lead", 2),
    tick("lead-a", "lead", 0.03),
  ];
  expect(project(events).header.cost).toBeCloseTo(0.15);
  expect(html(events)).toContain("Cost US$");
  events.push(tick("lead-a", "lead", null), tick("lead-a", "lead", 1));
  expect(project(events).header.cost).toBeNull();
  expect(html(events)).toContain("Cost unknown");
  const state = applyEvent(fold(events), {
    ...run,
    seq: 1,
    payload: { ...run.payload, run_id: "new-run" },
  });
  expect(state.leadCosts).toEqual({});
});
it.each(["tool_calls", "model_calls", "time", "spend_brake"] as const)(
  "uses reply-position limits for %s cut copy",
  (cut) => {
    const numeric = {
      tool_calls: "21 tool calls",
      model_calls: "32 model calls",
      time: "450 s",
      spend_brake: "the spending brake stopped the next model call",
    };
    const generic = {
      tool_calls: "tool call limit reached",
      model_calls: "model call limit reached",
      time: "time limit reached",
      spend_brake: numeric.spend_brake,
    };
    for (const known of [true, false]) {
      const events = [
        lead(1, "working", 4, { limits: known ? limits : null }),
        answer(cut),
        lead(2, "idle", 5, {
          limits: { ...limits, tool_calls: 999, model_calls: 888, wall_clock_s: 777 },
        }),
      ];
      const markup = renderToStaticMarkup(
        createElement(LeadThreadRows, { conversation: project(events) }),
      );
      expect(markup).toContain(
        `Turn ended: ${known ? numeric[cut] + (cut === "spend_brake" ? "" : " reached") : generic[cut]}`,
      );
      expect(markup).not.toContain("999");
    }
  },
);
it.each(["stopped", "halted", "done"] as const)(
  "releases cap only on authoritative %s, ignoring stale revisions",
  (terminal) => {
    const opening = { ...run, payload: { ...run.payload, max_leads: 2 } };
    const events = [lead(2, "idle", 2), lead(1, "working", 1, { agent_id: "lead-b" })];
    const state = fold(events, opening);
    expect(newLeadLabel(state)).toBe("New lead · 2 of 2");
    const ended = applyEvent(state, { ...lead(3, terminal, 2), seq: state.lastSeq + 1 });
    const stale = applyEvent(ended, { ...lead(2, "working", 2), seq: ended.lastSeq + 1 });
    expect(leadCapacity(stale)).toEqual({ active: 1, max: 2, atCap: false });
    expect(newLeadLabel(stale)).toBe("New lead");
    expect(leadCapacity(fold(events)).max).toBeNull();
  },
);
