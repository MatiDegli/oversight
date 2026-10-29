import type { DecisionRef, ServerEvent } from "@contracts";
import { expect, it } from "vitest";

import answers from "../../../fixtures/events/handwritten-inbox-answers.jsonl?raw";
import count from "../../../fixtures/events/handwritten-inbox-count.jsonl?raw";
import delegation from "../../../fixtures/events/handwritten-inbox-delegation.jsonl?raw";
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
  selectDecisionInbox as groupedInbox,
  selectDecisionInboxCount,
} from "./index";
import type { State } from "./types";

const selectDecisionInbox = (state: State) =>
  groupedInbox(state)
    .flatMap((entry) => entry.decisions)
    .filter((decision) => decision.kind !== "change");

const fixtures = { count, answers, delegation, empty, kinds, revisions };
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
      expect(groupedInbox(store.getState().state)).toEqual(groupedInbox(state));
    }
    const rebuilt = log.reduce(applyEvent, createInitialState());
    expect(rebuilt).toEqual(state);
    expect(groupedInbox(rebuilt)).toEqual(groupedInbox(state));
    expect(selectDecisionInboxCount(state)).toBe(groupedInbox(state).length);
    expect(new Set(groupedInbox(state).map((entry) => entry.id)).size).toBe(
      selectDecisionInboxCount(state),
    );
    expect(groupedInbox(JSON.parse(JSON.stringify(state)) as State)).toEqual(groupedInbox(state));
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
  expect(selectDecisionInboxCount(replay(kinds))).toBe(5);
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
  const tiedIds = after.slice(0, 2).map((entry) => entry.id);
  expect(tiedIds).toEqual([...tiedIds].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0)));
  const state = replay(kinds);
  const duplicate = events(kinds).at(-1);
  if (!duplicate) throw new Error("Missing duplicate");
  expect(selectDecisionInbox(applyEvent(state, duplicate))).toEqual(selectDecisionInbox(state));
  expect(selectDecisionInbox({ ...state, clock: null }).every((e) => e.age === "")).toBe(true);
});

it("breaks same-seq ties by code units for case- and accent-differing questions", () => {
  const log = events(kinds)
    .slice(0, 5)
    .map((event): ServerEvent => {
      if (event.type !== "change.facts_recorded") return event;
      const card = event.payload.review_cards?.[0];
      if (!card) throw new Error("Missing card");
      return {
        ...event,
        payload: {
          ...event.payload,
          review_cards: ["a?", "é?", "Z?", "E?"].map((question) => ({ ...card, question })),
        },
      };
    });
  const entries = selectDecisionInbox(log.reduce(applyEvent, createInitialState()));
  expect(entries.map((entry) => entry.firstSeq)).toEqual([5, 5, 5, 5]);
  expect(entries.map((entry) => entry.reason)).toEqual(["E?", "Z?", "a?", "é?"]);
  const ids = entries.map((entry) => entry.id);
  expect(ids).toEqual([...ids].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0)));
});

it("keeps every question after answers, conflict, answering echoes and repeated facts", () => {
  for (const end of [5, 6, 7, 8, 9, 10, 11]) {
    const entries = inbox(answers, end);
    expect(entries.filter((e) => e.kind === "review_card")).toHaveLength(4);
    expect(selectDecisionInboxCount(replay(answers, end))).toBe(1);
    expect(groupedInbox(replay(answers, end))[0]?.firstSeq).toBe(4);
  }
  expect(inbox(answers).filter((e) => e.kind === "follow_main")).toHaveLength(1);
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

it("counts changes through partial/all answers, final actions and new revisions", () => {
  const expected = [0, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 1, 1, 0, 1, 0];
  for (const [index, value] of expected.entries())
    expect(selectDecisionInboxCount(replay(count, index + 1)), `seq ${String(index + 1)}`).toBe(
      value,
    );
  const before = groupedInbox(replay(count, 5));
  const answered = groupedInbox(replay(count, 7));
  expect(answered.map((e) => [e.id, e.firstSeq, e.ts])).toEqual(
    before.map((e) => [e.id, e.firstSeq, e.ts]),
  );
  expect(answered[0]?.decisions.filter((d) => d.kind === "review_card")).toHaveLength(4);
  expect(groupedInbox(replay(count, 9))[0]?.attempts).toMatchObject([
    { agentId: "agent-2", endState: "halted" },
  ]);
  expect(groupedInbox(replay(count, 13))[0]).toMatchObject({ firstSeq: 12, attempts: [] });
  const grouped = groupedInbox(replay(kinds));
  expect(grouped.map((e) => e.firstSeq)).toEqual([4, 10, 12, 14, 18]);
  expect(grouped[0]?.decisions.map((d) => d.kind)).toEqual([
    "change",
    "review_card",
    "review_card",
    "checks_unavailable",
    "risky_area",
  ]);
});

it("keeps separate attempts when one agent answers different cards of a change", () => {
  const echo = events(count)[7];
  if (echo?.type !== "agent.status" || echo.payload.answering?.kind !== "review_card")
    throw new Error("Missing answering fixture");
  let state = replay(count, 5);
  const statuses = [
    { ...echo.payload, state: "working" as const },
    { ...echo.payload, state: "done" as const },
    {
      ...echo.payload,
      state: "working" as const,
      answering: { ...echo.payload.answering, question: "Keep yes?" },
    },
  ];
  for (const payload of statuses)
    state = applyEvent(state, { ...echo, seq: state.lastSeq + 1, payload });
  const entry = groupedInbox(state)[0];
  expect(entry?.decisions.flatMap((decision) => decision.attempts)).toHaveLength(2);
  expect(entry?.attempts).toEqual([
    { agentId: "agent-2", slot: "B", echoSeq: 6, endState: "done", endSeq: 7, reason: null },
    { agentId: "agent-2", slot: "B", echoSeq: 8, endState: null, endSeq: null, reason: null },
  ]);
  // A repeated status for the same task must not duplicate either attempt.
  state = applyEvent(state, {
    ...echo,
    seq: 9,
    payload: {
      ...echo.payload,
      state: "working",
      answering: { ...echo.payload.answering, question: "Keep yes?" },
    },
  });
  expect(groupedInbox(state)[0]?.attempts).toEqual(entry?.attempts);
  expect(selectDecisionInboxCount(state)).toBe(1);
});
