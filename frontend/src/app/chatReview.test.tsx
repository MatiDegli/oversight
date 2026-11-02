function required<T>(value: T | null | undefined): T {
  if (value == null) throw new Error("Missing fixture evidence");
  return value;
}
import { renderToStaticMarkup as html } from "react-dom/server";
import { readFileSync } from "node:fs";
import { useState } from "react";
import { expect, it } from "vitest";
import type { ServerEvent } from "@contracts";
import { applyEvent, createInitialState } from "../state";
import { chatReviewState } from "./chatReview.browser.fixture";
import { changeCost, selectChatChange } from "./chatReviewSelectors";
import { graphReviewAction, useChatReviewNavigation } from "./chatReviewNavigation";
import { ChatChangeCard } from "./shell/ChatChangeCard";
// eslint-disable-next-line no-restricted-imports -- Exercise only the optional timeline seam.
import { ConversationPanel } from "../agents/conversation/ConversationPanel";
// eslint-disable-next-line no-restricted-imports -- Preserve the built order and current entry identity.
import { selectConversation } from "../agents/conversation/selectConversation";
import { openReview, tabbedView, type View } from "./view";
import { resolvedReviewAction } from "./shell/ChatMerge";

it("keeps the C4 integration decision discoverable", () => {
  const decisions = readFileSync(new URL("../../../DECISIONS.md", import.meta.url), "utf8");
  expect(decisions).toMatch(/^## .* — C4 integration proof and the released cold editor gate$/m);
});

it("settles graph snapshots when phone practice recreates an equivalent view", () => {
  const state = chatReviewState();
  function RecreatedView() {
    const [entered, setEntered] = useState(false);
    const navigation = useChatReviewNavigation(
      state,
      { ...tabbedView, activeTab: entered ? "practice" : null },
      { layout: "Oversight", setLayout: () => {} },
    );
    if (!entered) setEntered(true);
    return <span>{navigation.graphView.activeTab}</span>;
  }
  expect(html(<RecreatedView />)).toBe("<span>practice</span>");
});

it.each(["selection", "review", "diff"] as const)(
  "preserves Oversight %s through run replacement and replay restart without a graph click",
  (target) => {
    const state = chatReviewState();
    const view: View = {
      ...tabbedView,
      selected: "shop.cart:Cart.quote_add",
      review: target === "review" ? "change-1" : null,
      diff: target === "diff" ? { changeId: "change-1", path: "shop/cart.py" } : null,
    };
    const replacement = {
      ...state,
      run: { ...required(state.run), run_id: "replacement-run" },
    };
    for (const next of [replacement, createInitialState()]) {
      function RunSwap() {
        const [phase, setPhase] = useState(0);
        const navigation = useChatReviewNavigation(phase === 0 ? state : next, view, {
          layout: "Oversight",
          setLayout: () => {},
        });
        // Check both the replacement render and the settled snapshot, with no view dispatch.
        if (phase < 2) setPhase(phase + 1);
        expect(navigation.graphView).toEqual(view);
        return null;
      }
      html(<RunSwap />);
    }
  },
);

const model = () => selectChatChange(chatReviewState(), "change-1");
it.each([true, false])(
  "identifies the comparison author with an agent slot present: %s",
  (present) => {
    const state = chatReviewState();
    if (!present) delete state.agents["agent-1"];
    const output = html(
      <ChatChangeCard state={state} changeId="change-1" seq={62} reviewing={false} />,
    );
    const author = present ? "A" : "agent-1";
    expect(output).toContain(`Agent ${author} says`);
    expect(output).toContain(`After · Agent ${author}</span>`);
  },
);
it("joins the exact retained proposal position, facts and assessment", () => {
  const state = chatReviewState(),
    card = model();
  expect(card?.seq).toBe(62);
  expect(card?.facts?.head_commit).toBe(card?.proposal.head_commit);
  expect(card?.callers.measured).toBe(3);
  expect(card?.examples[0]?.name).toBe("Cart.quote_add");
  expect(card?.callers.same).toHaveLength(1);
  expect(selectChatChange(state, "change-1", 56)).toBeNull();
  required(state.changes["change-1"]).proposalEvent = { seq: 56 };
  expect(selectChatChange(state, "change-1")).toBeNull();
});
it.each(["revision", "head_commit"] as const)(
  "does not borrow stale %s facts or assessments",
  (field) => {
    const state = chatReviewState();
    const proposal = required(required(state.changes["change-1"]).proposal);
    if (field === "revision") proposal.revision++;
    else proposal.head_commit = "replacement";
    // Retain a new exact envelope and position, just as another round does.
    const next = applyEvent(state, {
      seq: state.lastSeq + 1,
      ts: required(state.clock),
      type: "change.proposed",
      payload: { ...proposal },
    });
    const card = selectChatChange(next, "change-1");
    expect(card?.facts).toBeUndefined();
    expect(card?.passes).toBe(0);
    expect(card?.rules.every((rule) => rule.result === "absent")).toBe(true);
    expect(card?.callers.measured).toBe(0);
    expect(card?.examples).toEqual([]);
    expect(card?.waiting).toBe(true);
  },
);
it("keeps six results, failures, unknown and absent without counting extra rules", () => {
  const state = chatReviewState();
  const assessment = state.decisionEvents.findLast((e) => e.type === "change.assessed");
  if (assessment?.type !== "change.assessed") throw new Error("Missing assessment");
  required(assessment.payload.rules[0]).result = "fail";
  assessment.payload.rules.splice(1, 1);
  assessment.payload.rules.push({
    rule: "protected_paths",
    result: "pass",
    observed: null,
    threshold: {},
    evidence: [],
  });
  const card = selectChatChange(state, "change-1");
  expect(card?.rules).toHaveLength(6);
  expect(card?.passes).toBe(3);
  expect(card?.rules.map((r) => r.result)).toEqual([
    "fail",
    "absent",
    "pass",
    "pass",
    "pass",
    "unknown",
  ]);
  assessment.payload.rules = [];
  const empty = selectChatChange(state, "change-1");
  expect(empty?.rules.every((r) => r.result === "absent")).toBe(true);
  expect(empty?.passes).toBe(0);
});
it("labels every rule not assessed when no assessment has been recorded", () => {
  const state = chatReviewState();
  state.decisionEvents = state.decisionEvents.filter((event) => event.type !== "change.assessed");
  const card = selectChatChange(state, "change-1");
  expect(card?.rules).toHaveLength(6);
  expect(card?.rules.every((rule) => rule.result === "absent")).toBe(true);
  expect(card?.passes).toBe(0);
  const output = html(
    <ChatChangeCard state={state} changeId="change-1" seq={62} reviewing={false} />,
  );
  expect(output.match(/data-result="absent"/g)).toHaveLength(6);
  expect(output).toContain('title="Tests on the combined result: not assessed"');
  expect(output).toContain("Known spend: not assessed</span>");
  expect(output).not.toContain('data-result="unknown"');
});
it("keeps unavailable and unstable evidence honest without repeating stable examples", () => {
  const state = chatReviewState();
  expect(selectChatChange(state, "change-1")?.partial).toEqual([]);
  const facts = required(state.changeFacts["change-1"]?.at(-1));
  const caller = required(facts.measured_cone?.[0]);
  required(caller.rows[0]).unstable = true;
  const card = selectChatChange(state, "change-1");
  expect(card?.examples).toEqual([]);
  expect(card?.partial[0]?.line).toContain("results varied");
  facts.measured_cone = null;
  expect(selectChatChange(state, "change-1")?.callers.measured).toBe(0);
  state.usageTicks = [];
  const output = html(
    <ChatChangeCard state={state} changeId="change-1" seq={62} reviewing={false} />,
  );
  expect(output).toContain("Unknown");
  expect(output).not.toContain("US$0.00");
});
it("sums exact change costs across roles and agents; preserves missing and null", () => {
  let state = chatReviewState();
  const tick = required(state.usageTicks.at(-1));
  state.usageTicks = [];
  expect(changeCost(state, "change-1")).toBeNull();
  for (const [change_id, agent_id, role, cost_usd] of [
    ["change-1", "agent-1", "agent", 0.01],
    ["change-1", "reviewer", "review", 0.02],
    [null, "agent-1", "lead", 8],
    ["change-2", "agent-1", "plan", 9],
  ] as const)
    state = applyEvent(state, {
      seq: state.lastSeq + 1,
      ts: required(state.clock),
      type: "usage.tick",
      payload: { ...tick, change_id, agent_id, role, cost_usd },
    });
  expect(changeCost(state, "change-1")).toBeCloseTo(0.03);
  const nullCost: ServerEvent = {
    seq: state.lastSeq + 1,
    ts: required(state.clock),
    type: "usage.tick",
    payload: { ...tick, cost_usd: null },
  };
  const gap = applyEvent(state, { ...nullCost, seq: state.lastSeq + 2 });
  expect(gap.gap).toBe(true);
  expect(gap.usageTicks).toEqual(state.usageTicks);
  state = applyEvent(state, nullCost);
  state = applyEvent(state, {
    ...nullCost,
    seq: state.lastSeq + 1,
    payload: { ...tick, cost_usd: 0 },
  });
  expect(changeCost(state, "change-1")).toBeNull();
  const reset = applyEvent(state, {
    seq: 1,
    ts: required(state.clock),
    type: "run.started",
    payload: { ...required(state.run), run_id: "new-run" },
  });
  expect(reset.usageTicks).toEqual([]);
  expect(createInitialState().usageTicks).toEqual([]);
});
it("folds change costs without mutating earlier state or arrays", () => {
  let state = chatReviewState();
  const tick = required(state.usage);
  state = { ...state, usageTicks: [] };
  for (const [change_id, agent_id, role, cost_usd] of [
    ["change-1", "agent-1", "agent", 0.01],
    ["change-2", "agent-1", "plan", 0],
    [null, "agent-1", "lead", 8],
    ["change-1", "reviewer", "review", 0.02],
    [null, "planner", "plan", 2],
  ] as const)
    state = applyEvent(state, {
      seq: state.lastSeq + 1,
      ts: required(state.clock),
      type: "usage.tick",
      payload: { ...tick, change_id, agent_id, role, cost_usd },
    });
  const before = state;
  const array = before.usageTicks;
  const entries = [...array];
  const snapshot = structuredClone(before);
  const arraySnapshot = structuredClone(array);
  const incoming: Extract<ServerEvent, { type: "usage.tick" }> = {
    seq: before.lastSeq + 1,
    ts: required(before.clock),
    type: "usage.tick",
    payload: {
      ...tick,
      change_id: "change-1",
      agent_id: "agent-2",
      role: "review",
      cost_usd: 0.04,
    },
  };
  const payloadSnapshot = structuredClone(incoming.payload);
  array.forEach(Object.freeze);
  Object.freeze(array);
  Object.freeze(before);
  Object.freeze(incoming.payload);
  Object.freeze(incoming);
  state = applyEvent(before, incoming);
  expect(before.usageTicks).toBe(array);
  expect(before).toEqual(snapshot);
  expect(array).toEqual(arraySnapshot);
  entries.forEach((entry, index) => {
    expect(array[index]).toBe(entry);
  });
  expect(incoming.payload).toEqual(payloadSnapshot);
  expect(state).not.toBe(before);
  expect(state.usageTicks).not.toBe(array);
  expect(state.usage).toBe(incoming.payload);
  expect(state.lastSeq).toBe(incoming.seq);
  expect(state.gap).toBe(false);
  expect(state.usageTicks).toHaveLength(3);
  expect(state.usageTicks.map((entry) => entry.change_id)).toEqual(["change-2", null, "change-1"]);
  expect(state.usageTicks.at(-1)).toEqual({ ...incoming.payload, cost_usd: 0.07 });
  expect(state.usageTicks.find((entry) => entry.change_id === null)?.cost_usd).toBe(10);
  expect(changeCost(before, "change-1")).toBeCloseTo(0.03);
  expect(changeCost(state, "change-1")).toBeCloseTo(0.07);
  expect(changeCost(state, "change-2")).toBe(0);
  expect(changeCost(state, "missing")).toBeNull();
  for (const change_id of ["change-1", null])
    for (const cost_usd of [null, 0, 0.5]) {
      state = applyEvent(state, {
        ...incoming,
        seq: state.lastSeq + 1,
        payload: { ...incoming.payload, change_id, cost_usd },
      });
      expect(state.usageTicks).toHaveLength(3);
      expect(state.usageTicks.at(-1)).toEqual({ ...incoming.payload, change_id, cost_usd: null });
      expect(changeCost(state, "change-1")).toBeNull();
      expect(changeCost(state, "change-2")).toBe(0);
    }
});
it("only an exact final resolution settles this candidate; conflict keeps waiting", () => {
  const state = chatReviewState(),
    p = required(required(state.changes["change-1"]).proposal);
  const resolved: Extract<ServerEvent, { type: "change.resolved" }> = {
    seq: 67,
    ts: required(state.clock),
    type: "change.resolved",
    payload: {
      change_id: p.change_id,
      agent_id: p.agent_id,
      revision: p.revision,
      head_commit: p.head_commit,
      decision: "approved",
      merge: "merged",
      merge_commit: "merged",
      reason: null,
      comment: null,
      conflict: null,
      decided_by: "rules",
      review_seq: null,
      requested_by: null,
      reasons: [],
      assessment: null,
      grants: [],
      answers: [],
    },
  };
  expect(
    selectChatChange(
      applyEvent(state, { ...resolved, payload: { ...resolved.payload, revision: 0 } }),
      p.change_id,
    )?.waiting,
  ).toBe(true);
  expect(
    selectChatChange(
      applyEvent(state, { ...resolved, payload: { ...resolved.payload, head_commit: "old" } }),
      p.change_id,
    )?.waiting,
  ).toBe(true);
  const merged = applyEvent(state, resolved);
  expect(
    selectChatChange(
      applyEvent(state, {
        ...resolved,
        payload: {
          ...resolved.payload,
          merge: "conflict",
          merge_commit: null,
          conflict: { kind: "merge", paths: ["shop/pricing.py"], tests: null },
        },
      }),
      p.change_id,
    )?.waiting,
  ).toBe(true);
  expect(selectChatChange(merged, p.change_id)?.waiting).toBe(false);
  expect(openReview(merged, tabbedView, p.change_id)).toBeNull();
  const action = resolvedReviewAction(merged, tabbedView, {
    seq: 67,
    kind: "resolution",
    ts: resolved.ts,
    resolution: resolved.payload,
    round: 1,
  });
  expect(action?.type).toBe("open");
  if (action?.type === "open") expect(action.tab.frameIds).toEqual([]);
  const intent = { runId: required(state.run).run_id, proposal: p, key: "explicit-click-1" };
  expect(graphReviewAction(merged, intent)?.type).toBe("open");
  expect(graphReviewAction(merged, { ...intent, runId: "replaced" })).toBeNull();
  expect(
    graphReviewAction(merged, { ...intent, proposal: { ...p, head_commit: "old" } }),
  ).toBeNull();
});
it("replaces only the current entry, keeping ordinary rows, note attribution and unknown words", () => {
  const state = chatReviewState(),
    conversation = required(selectConversation(state, "agent-1", []));
  conversation.entries.push({
    kind: "proposed",
    seq: 1,
    ts: required(state.clock),
    summary: "Historical finish note",
  });
  const output = html(
    <ConversationPanel
      conversation={conversation}
      renderProposed={(entry) =>
        selectChatChange(state, "change-1", entry.seq) ? (
          <ChatChangeCard state={state} changeId="change-1" seq={entry.seq} reviewing={false} />
        ) : null
      }
    />,
  );
  expect(output.match(/aria-label="Proposed change"/g)).toHaveLength(1);
  expect(output).toContain("Historical finish note");
  expect(output).toContain("Agent A says");
  expect(output).toContain("Review is unavailable for this candidate.");
  expect(output).toContain("Six recorded rule results");
  expect(output.indexOf("Finished the change")).toBeLessThan(output.indexOf("Evidence 5 of 6"));
  expect(html(<ChatChangeCard state={state} changeId="change-1" seq={62} reviewing />)).toContain(
    "Reviewing in the panel",
  );
});
