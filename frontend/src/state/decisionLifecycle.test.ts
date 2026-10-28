import type { DecisionRef, ServerEvent } from "@contracts";
import { expect, it } from "vitest";

import kinds from "../../../fixtures/events/handwritten-inbox-kinds.jsonl?raw";
import { parseEvent } from "../transport";
import { applyEvent, createInitialState, selectDecisionInbox } from "./index";
import type { State } from "./types";

const log = kinds.trim().split("\n").map(parseEvent);
const replay = () => log.reduce(applyEvent, createInitialState());
function append(state: State, event: ServerEvent): State {
  return applyEvent(state, { ...event, seq: state.lastSeq + 1, ts: "2026-10-28T19:00:00.000Z" });
}
function delegation(state: State, answering: DecisionRef): State {
  const agent = state.agents["agent-2"];
  if (!agent) throw new Error("Missing fixture agent");
  return append(state, {
    seq: 0,
    ts: "",
    type: "agent.status",
    payload: {
      ...agent,
      examples: null,
      agent_id: "agent-6",
      slot: "B",
      change_id: "change-6",
      answering,
    },
  });
}

const refKinds = [
  "review_card",
  "checks_unavailable",
  "follow_main",
  "risky_area",
  "examples_before_code",
  "lead_plan",
] as const;

it.each(refKinds)(
  "closes only the matching %s on an answering echo, across every later status",
  (kind) => {
    const state = replay();
    const before = selectDecisionInbox(state);
    const entry = before.find((e) => e.identity.kind === kind);
    if (!entry || entry.identity.kind === "revert_needs_you")
      throw new Error("Missing fixture entry");
    const ref = entry.identity;
    const stale = delegation(state, { ...ref, evidence_seq: ref.evidence_seq + 1 });
    expect(selectDecisionInbox(stale).map((e) => e.id)).toEqual(before.map((e) => e.id));
    const wrongRevision = delegation(state, { ...ref, revision: ref.revision + 1 });
    expect(selectDecisionInbox(wrongRevision).map((e) => e.id)).toEqual(before.map((e) => e.id));
    const after = delegation(state, ref);
    expect(selectDecisionInbox(after).map((e) => e.id)).toEqual(
      before.filter((e) => e.id !== entry.id).map((e) => e.id),
    );
    const agent = after.agents["agent-6"];
    if (!agent) throw new Error("Missing delegation agent");
    const stopped = append(after, {
      seq: 0,
      ts: "",
      type: "agent.status",
      payload: { ...agent, state: "stopped", answering: null },
    });
    expect(selectDecisionInbox(stopped).map((e) => e.id)).toEqual(
      selectDecisionInbox(after).map((e) => e.id),
    );
  },
);

it("resolves only the exact candidate, retaining conflict after approve and Unsure", () => {
  const state = replay();
  const resolution = log.find(
    (e) => e.type === "change.resolved" && e.payload.merge === "conflict",
  );
  if (resolution?.type !== "change.resolved") throw new Error("Missing conflict");
  const stale = append(state, {
    ...resolution,
    payload: {
      ...resolution.payload,
      head_commit: "stale",
      decision: "rejected",
      merge: null,
      conflict: null,
    },
  });
  expect(selectDecisionInbox(stale).map((e) => e.id)).toEqual(
    selectDecisionInbox(state).map((e) => e.id),
  );
  const closed = append(state, {
    ...resolution,
    payload: {
      ...resolution.payload,
      decision: "changes_requested",
      merge: null,
      conflict: null,
    },
  });
  expect(selectDecisionInbox(closed).some((e) => e.kind === "follow_main")).toBe(false);
  expect(selectDecisionInbox(closed).filter((e) => e.changeId === "change-1")).toHaveLength(4);
});

it("closes a started plan and preserves lead revision identity independently of plan revisions", () => {
  const state = replay();
  const plan = log.find((e) => e.type === "plan.updated");
  const lead = log.find((e) => e.type === "lead.status");
  if (plan?.type !== "plan.updated" || lead?.type !== "lead.status")
    throw new Error("Missing plan");
  const entry = selectDecisionInbox(state).find((e) => e.identity.kind === "lead_plan");
  if (entry?.identity.kind !== "lead_plan") throw new Error("Missing lead entry");
  const delegated = delegation(state, entry.identity);
  const updated = append(delegated, { ...lead, payload: { ...lead.payload, revision: 5 } });
  expect(selectDecisionInbox(updated).find((e) => e.kind === "lead_plan")?.identity).toMatchObject({
    revision: 5,
    evidence_seq: updated.lastSeq,
  });
  const started = append(state, {
    ...plan,
    payload: { ...plan.payload, revision: 10, state: "starting" },
  });
  expect(selectDecisionInbox(started).some((e) => e.kind === "lead_plan")).toBe(false);
  const ambiguous = append(state, { ...plan, payload: { ...plan.payload, plan_id: "plan-2" } });
  expect(selectDecisionInbox(ambiguous).some((e) => e.kind === "lead_plan")).toBe(false);
  const unlinked = append(log.slice(0, 2).reduce(applyEvent, createInitialState()), {
    ...plan,
    payload: { ...plan.payload, started_by: null },
  });
  expect(selectDecisionInbox(unlinked)).toEqual([]);
});

it("keeps Revert separate from change resolutions and closes only its recorded inverse", () => {
  const state = replay();
  const revert = log.find((e) => e.type === "revert.resolved");
  const resolution = log.find(
    (e) => e.type === "change.resolved" && e.payload.decided_by === "rules",
  );
  if (revert?.type !== "revert.resolved" || resolution?.type !== "change.resolved")
    throw new Error("Missing Revert");
  const inverse = {
    ...revert,
    payload: {
      ...revert.payload,
      result: "reverted" as const,
      main_commit: "inverse",
      revert_commit: "inverse",
      tests: {
        outcome: "passed" as const,
        passed: 1,
        failed: 0,
        skipped: 0,
        duration_ms: 1,
        failed_tests: [],
        cases: null,
        cases_complete: null,
      },
    },
  };
  const unrelated = append(state, {
    ...inverse,
    payload: { ...inverse.payload, resolution_seq: 999 },
  });
  expect(selectDecisionInbox(unrelated).some((e) => e.kind === "revert_needs_you")).toBe(true);
  const closed = append(state, inverse);
  expect(selectDecisionInbox(closed).some((e) => e.kind === "revert_needs_you")).toBe(false);
  expect(selectDecisionInbox(closed)).toHaveLength(selectDecisionInbox(state).length - 1);
});

it("closes confirmed examples and selects the smallest recorded proposal example", () => {
  const state = replay();
  const status = state.agents["agent-2"];
  const proposal = log.find((e) => e.type === "change.proposed");
  if (!status || proposal?.type !== "change.proposed") throw new Error("Missing examples fixture");
  const confirmed = append(state, {
    seq: 0,
    ts: "",
    type: "agent.status",
    payload: {
      ...status,
      examples: (status.examples ?? []).map((e) => ({
        ...e,
        pin: { source_task: status.task, confirmed_by: "person" },
      })),
    },
  });
  expect(selectDecisionInbox(confirmed).some((e) => e.kind === "examples_before_code")).toBe(false);
  const proposed = append(state, {
    ...proposal,
    payload: {
      ...proposal.payload,
      change_id: "change-2",
      agent_id: "agent-2",
      examples: [
        {
          example_id: "large",
          inputs: "price=100, tax=10, discount=5",
          expected_output: "105",
          expected_behavior: null,
          note: null,
          pin: null,
        },
        {
          example_id: "small",
          inputs: "price=0",
          expected_output: "0",
          expected_behavior: null,
          note: null,
          pin: null,
        },
      ],
    },
  });
  expect(
    selectDecisionInbox(proposed).find((e) => e.kind === "examples_before_code")?.example,
  ).toMatchObject({ source: "task", value: { example_id: "small" } });
});

it("closes all entries of a resolved candidate while preserving explicitly Unsure cards", () => {
  const state = replay();
  const conflict = log.find((e) => e.type === "change.resolved" && e.payload.merge === "conflict");
  const fact = log.find((e) => e.type === "change.facts_recorded");
  if (conflict?.type !== "change.resolved" || fact?.type !== "change.facts_recorded")
    throw new Error("Missing candidate");
  const target = {
    ...conflict.payload,
    change_id: "change-1",
    agent_id: "agent-1",
    head_commit: "head",
    decision: "changes_requested" as const,
    merge: null,
    conflict: null,
  };
  const resolved = append(state, { ...conflict, payload: target });
  expect(selectDecisionInbox(resolved).filter((e) => e.changeId === "change-1")).toHaveLength(0);
  const unsure = append(state, {
    ...conflict,
    payload: {
      ...target,
      answers:
        fact.payload.review_cards?.map((card) => ({
          ...card,
          answer: "unsure",
          answered_by: "person",
        })) ?? null,
    },
  });
  expect(selectDecisionInbox(unsure).filter((e) => e.changeId === "change-1")).toHaveLength(2);
});
