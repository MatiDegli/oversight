import { expect, it } from "vitest";

// eslint-disable-next-line no-restricted-imports -- Reuse line A's hand-written fixture without changing review.
import {
  behaviorEvents,
  behaviorFacts,
  behaviorState,
  required,
  withBehaviorFacts,
} from "../review/behavior.fixture";
import { applyEvent, createInitialState } from "../state";
import { selectGraphBehavior } from "./behavior";

const available = required(behaviorFacts[0]);
const probe = required(required(available.behavior_diff)[0]);
const change = required(behaviorState.changes["change-1"]);
const proposal = required(change.proposal);

it("projects the fixture's published nodes, statuses, counts and limitations", () => {
  const state = applyEvent(behaviorState, required(behaviorEvents[0]));
  expect(selectGraphBehavior(state)).toEqual([
    {
      nodeId: probe.node_id,
      changeId: "change-1",
      agentId: "agent-1",
      status: "Available",
      compared: 10,
      differing: 9,
      unstable: false,
      noDifference: false,
      limitation: null,
    },
  ]);
  const partial = selectGraphBehavior(applyEvent(state, required(behaviorEvents[1])));
  expect(
    partial.map((entry) => [entry.nodeId, entry.status, entry.compared, entry.differing]),
  ).toEqual([
    [probe.node_id, "Partial", 2, 0],
    ["shop.cart:Cart.total", "Unavailable", 0, 0],
    ["removed.module:old_function", "Unavailable", 0, 0],
  ]);
  expect(partial.map((entry) => entry.unstable)).toEqual([true, false, false]);
  expect(partial.map((entry) => entry.noDifference)).toEqual([false, false, false]);
  expect(partial.map((entry) => entry.limitation)).toEqual([
    "Behavior probing was unavailable; no further reason was published.",
    "Methods and classes are not probed yet.",
    "The base or proposed version is unavailable.",
  ]);
});

it.each([
  { ...proposal, revision: proposal.revision + 1 },
  { ...proposal, head_commit: "replacement-head" },
])("drops superseded evidence and admits only the new candidate's facts", (next) => {
  const state = applyEvent(withBehaviorFacts(available), {
    seq: behaviorState.lastSeq + 1,
    ts: "2026-10-30T15:00:00Z",
    type: "change.proposed",
    payload: next,
  });
  expect(selectGraphBehavior(state)).toEqual([]);
  expect(
    selectGraphBehavior({
      ...state,
      changeFacts: {
        "change-1": [
          available,
          {
            ...available,
            revision: next.revision,
            head_commit: next.head_commit,
          },
        ],
      },
    }),
  ).toHaveLength(1);
});

it.each(["approved", "rejected", "changes_requested"] as const)(
  "drops evidence after a %s resolution",
  (decision) => {
    const state = applyEvent(withBehaviorFacts(available), {
      seq: behaviorState.lastSeq + 1,
      ts: "2026-10-30T15:00:00Z",
      type: "change.resolved",
      payload: {
        change_id: "change-1",
        agent_id: "agent-1",
        revision: proposal.revision,
        head_commit: proposal.head_commit,
        decision,
        comment: "Reviewed",
        reason: null,
        merge: decision === "approved" ? "merged" : null,
        merge_commit: decision === "approved" ? "merged-head" : null,
        conflict: null,
        decided_by: "person",
        review_seq: null,
        requested_by: null,
        reasons: [],
        assessment: null,
        answers: null,
      },
    });
    expect(selectGraphBehavior(state)).toEqual([]);
  },
);

it("keeps two changes for one node in change-id order independent of insertion order", () => {
  const state = withBehaviorFacts(available);
  const second = {
    ...change,
    change_id: "change-2",
    agent_id: "agent-2",
    proposal: { ...proposal, change_id: "change-2", agent_id: "agent-2" },
  };
  const changes = { "change-2": second, ...state.changes };
  const changeFacts = {
    ...state.changeFacts,
    "change-2": [{ ...available, change_id: "change-2" }],
  };
  const entries = selectGraphBehavior({ ...state, changes, changeFacts });
  expect(entries.map((entry) => [entry.nodeId, entry.changeId, entry.agentId])).toEqual([
    [probe.node_id, "change-1", "agent-1"],
    [probe.node_id, "change-2", "agent-2"],
  ]);
  expect(
    selectGraphBehavior({
      ...state,
      changes: { ...state.changes, "change-2": second },
      changeFacts,
    }),
  ).toEqual(entries);
});

it("keeps missing, null and empty evidence distinct from measured no difference", () => {
  for (const state of [
    createInitialState(),
    withBehaviorFacts(),
    withBehaviorFacts(required(behaviorFacts[2])),
    withBehaviorFacts(required(behaviorFacts[3])),
  ]) {
    expect(selectGraphBehavior(state)).toEqual([]);
  }
  expect(
    selectGraphBehavior(
      withBehaviorFacts({
        ...available,
        behavior_diff: [{ ...probe, compared_count: 1, differing_count: 0, rows: [] }],
      }),
    )[0],
  ).toMatchObject({ status: "Available", noDifference: true, compared: 1, differing: 0 });
});
