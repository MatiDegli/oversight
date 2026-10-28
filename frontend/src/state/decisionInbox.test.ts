import type { DecisionRef, ServerEvent } from "@contracts";
import { expect, it } from "vitest";

import answers from "../../../fixtures/events/handwritten-inbox-answers.jsonl?raw";
import empty from "../../../fixtures/events/handwritten-inbox-empty.jsonl?raw";
import kinds from "../../../fixtures/events/handwritten-inbox-kinds.jsonl?raw";
import revisions from "../../../fixtures/events/handwritten-inbox-revisions.jsonl?raw";
import { parseEvent } from "../transport";
import {
  applyEvent,
  createInitialState,
  createStateStore,
  decisionIdentityKey,
  matchesDecision,
  selectDecisionInbox,
  selectDecisionInboxCount,
} from "./index";
import type { State } from "./types";

const fixtures = { answers, empty, kinds, revisions };
const events = (text: string): ServerEvent[] => text.trim().split("\n").map(parseEvent);
const replay = (text: string, end?: number): State =>
  events(text).slice(0, end).reduce(applyEvent, createInitialState());
const inbox = (text: string, end?: number) => selectDecisionInbox(replay(text, end));

function freeze(value: unknown): void {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return;
  Object.freeze(value);
  for (const child of Object.values(value)) freeze(child);
}

it.each(Object.entries(fixtures))(
  "rebuilds %s from scratch and live batches without side effects",
  (_name, text) => {
    const log = events(text);
    const store = createStateStore();
    let state = createInitialState();
    freeze(log);
    for (const event of log) {
      freeze(state);
      state = applyEvent(state, event);
      expect(state.gap).toBe(false);
      store.getState().applyEvents([event]);
      expect(selectDecisionInbox(store.getState().state)).toEqual(selectDecisionInbox(state));
    }
    const rebuilt = log.reduce(applyEvent, createInitialState());
    expect(rebuilt).toEqual(state);
    expect(selectDecisionInbox(rebuilt)).toEqual(selectDecisionInbox(state));
    expect(selectDecisionInboxCount(state)).toBe(selectDecisionInbox(state).length);
    expect(new Set(selectDecisionInbox(state).map((entry) => entry.id)).size).toBe(
      selectDecisionInboxCount(state),
    );
    expect(selectDecisionInbox(JSON.parse(JSON.stringify(state)) as State)).toEqual(
      selectDecisionInbox(state),
    );
  },
);

it("retains seq/ts for facts, assessments, plans, leads, resolutions and answering statuses", () => {
  const state = replay(kinds);
  const types = new Set(state.decisionEvents.map((e) => e.type));
  expect(types).toEqual(
    new Set([
      "agent.status",
      "change.proposed",
      "change.facts_recorded",
      "change.assessed",
      "error",
      "lead.status",
      "plan.updated",
      "change.resolved",
      "revert.resolved",
    ]),
  );
  for (const event of state.decisionEvents) expect(event).toEqual(events(kinds)[event.seq - 1]);
  const history = replay(answers).decisionEvents;
  expect(history.find((e) => e.type === "change.resolved")?.payload).toMatchObject({
    answers: [
      expect.objectContaining({ answer: "yes" }),
      expect.objectContaining({ answer: "no" }),
      expect.objectContaining({ answer: "unsure" }),
    ],
    conflict: { kind: "merge" },
  });
  expect(history.filter((e) => e.type === "agent.status" && e.payload.answering)).toHaveLength(2);
  expect(replay(revisions).decisionEvents.filter((e) => e.type === "change.resolved")).toHaveLength(
    3,
  );
});

it("projects seven kinds, keeps every card, groups checks and preserves the D6 example", () => {
  const entries = inbox(kinds);
  expect(entries.map((e) => e.kind)).toEqual([
    "review_card",
    "review_card",
    "checks_unavailable",
    "risky_area",
    "examples_before_code",
    "lead_plan",
    "follow_main",
    "revert_needs_you",
  ]);
  const checks = entries.filter((e) => e.kind === "checks_unavailable");
  expect(checks).toHaveLength(1);
  expect(checks[0]?.reason).toContain("Coverage unavailable");
  expect(checks[0]?.reason).toContain("Unavailable check: combined_tests");
  const d6 = entries.find(
    (e) => e.reason === "Oversight could not tell whether this was asked for",
  );
  expect(d6?.example).toEqual({
    source: "review_card",
    value: { inputs: "price=0, discount=1", before: "int:0", after: "int:-1", unstable: false },
  });
  expect(entries.find((e) => e.kind === "examples_before_code")).toMatchObject({
    agentId: "agent-2",
    slot: "C",
    example: { source: "task" },
  });
  expect(entries.find((e) => e.kind === "follow_main")).toMatchObject({
    agentId: "agent-3",
    slot: "F",
  });
  expect(entries.find((e) => e.kind === "lead_plan")?.identity).toMatchObject({
    agent_id: "agent-5",
    revision: 4,
  });
  expect(entries.find((e) => e.kind === "lead_plan")?.planId).toBe("plan-1");
  expect(checks[0]?.example).toEqual({ source: "none", message: "No recorded example" });
  expect(selectDecisionInboxCount(replay(kinds))).toBe(8);
});

it("waits for terminal check evidence, and does not turn command errors into decisions", () => {
  expect(inbox(kinds, 6).map((e) => e.kind)).toEqual(["review_card", "review_card"]);
  expect(inbox(kinds, 7).filter((e) => e.kind === "checks_unavailable")).toHaveLength(1);
  expect(inbox(answers, 7).map((e) => [e.id, e.firstSeq])).toEqual(
    inbox(answers, 6).map((e) => [e.id, e.firstSeq]),
  );
});

it("uses the first raising seq/ts and a stable identity tie-break, even for repeated payloads", () => {
  const before = inbox(kinds, 18);
  const after = inbox(kinds);
  expect(after.map((e) => [e.id, e.firstSeq, e.ts])).toEqual(
    before.map((e) => [e.id, e.firstSeq, e.ts]),
  );
  expect(after.map((e) => e.firstSeq)).toEqual([5, 5, 7, 9, 10, 12, 15, 18]);
  expect(after[0]?.age).toBe("2 min ago");
  expect(after[0]?.id.localeCompare(after[1]?.id ?? "")).toBeLessThan(0);
  const state = replay(kinds);
  const duplicate = events(kinds).at(-1);
  if (!duplicate) throw new Error("Missing duplicate");
  expect(selectDecisionInbox(applyEvent(state, duplicate))).toEqual(selectDecisionInbox(state));
  expect(selectDecisionInbox({ ...state, clock: null }).every((e) => e.age === "")).toBe(true);
});

it("closes Yes and No alone, retains Unsure and the approve conflict, and requires persisted delegation", () => {
  expect(inbox(answers, 5).filter((e) => e.kind === "review_card")).toHaveLength(4);
  const before = inbox(answers, 7);
  expect(before.map((e) => e.reason)).toEqual([
    "Delegate this?",
    "Keep unsure?",
    "Merge conflict while following main: shop/pricing.py",
  ]);
  const after = inbox(answers);
  expect(after.map((e) => e.reason)).toEqual([
    "Keep unsure?",
    "Merge conflict while following main: shop/pricing.py",
  ]);
  expect(after[0]?.firstSeq).toBe(5);
  expect(replay(answers).agents["agent-2"]?.answering).toBeNull();
});

it("never inherits old answers or delegation across revisions or heads, nor treats personActed as an answer", () => {
  const oldRevision = inbox(revisions, 10);
  expect(oldRevision).toHaveLength(1);
  expect(oldRevision[0]?.identity).toMatchObject({
    revision: 2,
    head_commit: "head-2",
    evidence_seq: 8,
  });
  const newHead = inbox(revisions);
  expect(newHead).toHaveLength(1);
  expect(newHead[0]?.identity).toMatchObject({
    revision: 2,
    head_commit: "head-2b",
    evidence_seq: 12,
  });
  expect(newHead[0]?.firstSeq).toBe(12);
  expect(replay(revisions).changes["change-1"]?.personActed).toBe(true);
});

it("matches every DecisionRef field without conflating evidence, card, kind, candidate or lead revision", () => {
  const entry = inbox(answers, 5)[0];
  if (entry?.identity.kind !== "review_card") throw new Error("Missing card");
  const ref = entry.identity;
  expect(matchesDecision(ref, ref)).toBe(true);
  const mismatches: DecisionRef[] = [
    { ...ref, evidence_seq: ref.evidence_seq + 1 },
    { ...ref, revision: 2 },
    { ...ref, head_commit: "another" },
    { ...ref, change_id: "change-2" },
    { ...ref, node_id: "another" },
    { ...ref, question: "another" },
    {
      kind: "checks_unavailable",
      change_id: ref.change_id,
      revision: ref.revision,
      head_commit: ref.head_commit,
      evidence_seq: ref.evidence_seq,
    },
    { kind: "lead_plan", agent_id: "agent-1", revision: 1, evidence_seq: ref.evidence_seq },
  ];
  for (const other of mismatches) expect(matchesDecision(other, ref)).toBe(false);
  expect(decisionIdentityKey({ ...ref, evidence_seq: 999 })).toBe(decisionIdentityKey(ref));
});

it("returns no decisions for an empty run and resets all projection history on another run", () => {
  expect(selectDecisionInbox(createInitialState())).toEqual([]);
  expect(selectDecisionInboxCount(createInitialState())).toBe(0);
  expect(inbox(empty)).toEqual([]);
  expect(selectDecisionInboxCount(replay(empty))).toBe(0);
  const first = events(empty)[0];
  if (first?.type !== "run.started") throw new Error("Missing run");
  const reset = applyEvent(replay(kinds), {
    ...first,
    payload: { ...first.payload, run_id: "run-20261028-190000" },
  });
  expect(reset.decisionEvents).toEqual([]);
  expect(selectDecisionInbox(reset)).toEqual([]);
});
