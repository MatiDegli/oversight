import type { ServerEvent } from "@contracts";
import { expect, it } from "vitest";

import delegation from "../../../fixtures/events/handwritten-inbox-delegation.jsonl?raw";
import { parseEvent } from "../transport";
import { applyEvent, createInitialState, selectDecisionInbox as groupedInbox } from "./index";
import type { State } from "./types";

const selectDecisionInbox = (state: State) =>
  groupedInbox(state)
    .flatMap((entry) => entry.decisions)
    .filter((decision) => decision.kind !== "change");

const log = delegation.trim().split("\n").map(parseEvent);
const replay = (end = log.length) => log.slice(0, end).reduce(applyEvent, createInitialState());
const retry = (state: State) =>
  selectDecisionInbox(state).find((entry) => entry.reason === "Retry delegation?");

function eventAt(seq: number): ServerEvent {
  const event = log[seq - 1];
  if (!event) throw new Error(`Missing fixture event ${String(seq)}`);
  return event;
}

function append(state: State, event: ServerEvent): State {
  return applyEvent(state, { ...event, seq: state.lastSeq + 1 });
}

const firstAttempt = {
  agentId: "agent-2",
  slot: "B",
  endState: "halted",
  reason: "No progress after four identical calls",
  echoSeq: 6,
  endSeq: 8,
};
const secondAttempt = {
  agentId: "agent-3",
  slot: "C",
  endState: "stopped",
  reason: null,
  echoSeq: 11,
  endSeq: 12,
};

it("replays every step: halt, stop and proposal, retaining identity, order and attempts", () => {
  const expectedQuestions = log.map((event) =>
    event.seq < 5 ? [] : ["Finish delegation?", "Retry delegation?"],
  );
  const original = retry(replay(5));
  expect(original).toBeDefined();
  let state = createInitialState();
  for (const [index, event] of log.entries()) {
    state = applyEvent(state, event);
    const entries = selectDecisionInbox(state);
    expect(state.gap, `seq ${String(event.seq)}`).toBe(false);
    expect(
      entries.map((entry) => entry.reason),
      `seq ${String(event.seq)}`,
    ).toEqual(expectedQuestions[index]);
    expect(entries).toEqual(selectDecisionInbox(replay(index + 1)));
    const reopened = retry(state);
    if (reopened) {
      expect(reopened).toMatchObject({
        id: original?.id,
        identity: original?.identity,
        firstSeq: 5,
        ts: original?.ts,
        agentId: "agent-1",
        changeId: "change-1",
      });
      expect(reopened.age).toBe(selectDecisionInbox(state)[0]?.age);
      expect(reopened.attempts).toEqual(
        event.seq >= 15
          ? [
              firstAttempt,
              secondAttempt,
              {
                agentId: "agent-4",
                slot: "D",
                echoSeq: 15,
                endState: event.seq >= 17 ? "halted" : null,
                endSeq: event.seq >= 17 ? 17 : null,
                reason: event.seq >= 17 ? "Ended after proposing" : null,
              },
            ]
          : event.seq >= 12
            ? [firstAttempt, secondAttempt]
            : event.seq >= 11
              ? [firstAttempt, { ...secondAttempt, endState: null, reason: null, endSeq: null }]
              : event.seq >= 8
                ? [firstAttempt]
                : event.seq >= 6
                  ? [{ ...firstAttempt, endState: null, reason: null, endSeq: null }]
                  : [],
      );
    }
  }
  expect(state).toEqual(replay());
});

it("retains attempts across proposals and repeated statuses", () => {
  const proposal = eventAt(16);
  if (proposal.type !== "change.proposed") throw new Error("Missing proposal");
  const proposed = {
    ...proposal,
    payload: { ...proposal.payload, agent_id: "agent-2", change_id: "change-2" },
  };
  // Proposals never consume the originating decision.
  let state = append(replay(5), proposed);
  state = append(state, eventAt(6));
  state = append(state, eventAt(8));
  expect(retry(state)?.attempts).toHaveLength(1);
  state = append(state, proposed);
  expect(retry(state)).toBeDefined();
  state = append(state, eventAt(9));
  expect(retry(state)).toBeDefined();
  // The same holds when a proposal precedes the failure.
  state = append(replay(7), proposed);
  state = append(state, eventAt(8));
  expect(retry(state)).toBeDefined();
});

it("ignores another agent's failure or proposal and retains links absent from later statuses", () => {
  const end = eventAt(8);
  const proposal = eventAt(16);
  if (end.type !== "agent.status" || proposal.type !== "change.proposed")
    throw new Error("Missing delegation");
  const unrelated = append(replay(7), {
    ...end,
    payload: { ...end.payload, agent_id: "agent-6", answering: null },
  });
  expect(retry(unrelated)).toBeDefined();
  const reopened = append(unrelated, { ...end, payload: { ...end.payload, answering: null } });
  expect(retry(reopened)?.attempts).toEqual([{ ...firstAttempt, endSeq: 9 }]);
  expect(retry(append(reopened, proposal))?.attempts).toHaveLength(1);
});

it("retains done and overlapping active delegations after one fails", () => {
  const end = eventAt(8);
  const echo = eventAt(11);
  if (end.type !== "agent.status" || echo.type !== "agent.status")
    throw new Error("Missing delegation");
  const done = append(replay(7), { ...end, payload: { ...end.payload, state: "done" } });
  expect(retry(done)?.attempts[0]?.endState).toBe("done");
  expect(retry(append(done, end))?.attempts[0]?.endState).toBe("done");
  const recovered = append(replay(14), {
    ...end,
    payload: { ...end.payload, state: "done" },
  });
  expect(retry(recovered)).toBeDefined();
  const overlapping = append(replay(7), echo);
  expect(retry(append(overlapping, end))?.attempts).toHaveLength(2);
});

it.each(["yes", "no", "resolution"] as const)(
  "only a final resolution closes after a failed delegation (%s)",
  (answer) => {
    const facts = eventAt(5);
    if (facts.type !== "change.facts_recorded") throw new Error("Missing facts");
    let state = replay(14);
    expect(retry(state)?.attempts).toHaveLength(2);
    if (answer === "resolution") {
      state = append(state, {
        seq: 0,
        ts: facts.ts,
        type: "change.resolved",
        payload: {
          change_id: "change-1",
          agent_id: "agent-1",
          revision: 1,
          decision: "changes_requested",
          reason: null,
          comment: "Revise it",
          merge: null,
          merge_commit: null,
          conflict: null,
          decided_by: "person",
          review_seq: null,
          requested_by: null,
          head_commit: "head",
          reasons: [],
          assessment: null,
          answers: null,
        },
      });
    } else {
      state = append(state, {
        ...facts,
        payload: {
          ...facts.payload,
          review_cards:
            facts.payload.review_cards?.map((card) =>
              card.question === "Retry delegation?"
                ? { ...card, answer, answered_by: "person" }
                : card,
            ) ?? null,
        },
      });
    }
    expect(retry(state) === undefined).toBe(answer === "resolution");
    expect(retry(append(state, eventAt(13))) === undefined).toBe(answer === "resolution");
  },
);

it("resets attempts with the run and does not recover them from repeated evidence", () => {
  const start = eventAt(1);
  if (start.type !== "run.started") throw new Error("Missing run");
  let state = applyEvent(replay(14), {
    ...start,
    payload: { ...start.payload, run_id: "run-20261028-190000" },
  });
  expect(state.decisionEvents).toEqual([]);
  expect(selectDecisionInbox(state)).toEqual([]);
  for (const event of log.slice(1, 5)) state = applyEvent(state, event);
  expect(retry(state)?.attempts).toEqual([]);
});
