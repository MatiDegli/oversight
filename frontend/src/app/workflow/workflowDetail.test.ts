import { expect, it } from "vitest";
import {
  applyEvent,
  createInitialState,
  flowSteps,
  type PlanFlowRow,
  type State,
} from "../../state";
// eslint-disable-next-line no-restricted-imports -- Existing synthetic envelopes, no provider calls.
import { agent, event, proposal, run } from "../../state/state.support";
import { workflowDetail } from "./workflowDetail";

const row: PlanFlowRow = {
  index: 4,
  wave: 2,
  task: {
    task: "Full task with the exact recorded wording",
    depends_on: [3, 0],
    zone: { node_ids: [], paths: [] },
    agent_id: "unused-task-id",
    state: "waiting",
  },
  agentId: "projected-agent",
  changeId: proposal.change_id,
  word: "Waiting for you",
  condition: "Projected condition, preserved verbatim",
  progress: "Step 0 of 30",
  note: "Exact projected note",
  steps: [
    { mark: "passed", detail: "Work evidence" },
    { mark: "failed", detail: "Proposal evidence" },
    { mark: "waiting", detail: "Approval evidence" },
    { mark: "not needed", detail: "Combined evidence" },
    { mark: "not known", detail: "Merge evidence" },
  ],
  main: "not known",
  merged: false,
};
function proposed() {
  return [
    event(1, "run.started", run),
    event(2, "agent.status", agent),
    event(3, "change.proposed", proposal),
  ].reduce(applyEvent, createInitialState());
}
it("maps only the projected fields, local wave/dependencies and shared gate labels", () => {
  expect(workflowDetail(row, proposed(), true)).toEqual({
    task: row.task.task,
    word: row.word,
    condition: row.condition,
    wave: 3,
    dependencies: ["#4", "#1"],
    progress: row.progress,
    note: row.note,
    agent: row.agentId,
    role: "Not known",
    model: "Not known",
    mode: "Not known",
    gates: row.steps.map((step, i) => ({ label: flowSteps[i], ...step })),
    reviewId: proposal.change_id,
  });
});
it("leaves absent evidence unknown and empty dependencies exact", () => {
  const detail = workflowDetail(
    {
      ...row,
      progress: null,
      note: null,
      agentId: null,
      changeId: null,
      task: { ...row.task, depends_on: [] },
    },
    createInitialState(),
    true,
  );
  expect(detail).toMatchObject({
    progress: "Not known",
    note: "Not known",
    agent: "Not known",
    role: "Not known",
    model: "Not known",
    mode: "Not known",
    dependencies: [],
    reviewId: null,
  });
});
it.each(["Working", "Waiting for you", "Merged", "Not merged", "Waiting", "Not known"] as const)(
  "preserves the projected state word %s",
  (word) => {
    expect(workflowDetail({ ...row, word }, proposed(), false).word).toBe(word);
  },
);
it.each(["ready_for_review", "conflict", "editing", "revising", "approved", "rejected"] as const)(
  "uses the card's Review eligibility for %s",
  (status) => {
    const state = proposed();
    const change = state.changes[proposal.change_id];
    if (!change) throw new Error("Missing synthetic change");
    const candidate: State = {
      ...state,
      changes: { ...state.changes, [proposal.change_id]: { ...change, status } },
    };
    expect(workflowDetail(row, candidate, true).reviewId).toBe(
      ["ready_for_review", "conflict"].includes(status) ? proposal.change_id : null,
    );
    expect(workflowDetail(row, candidate, false).reviewId).toBeNull();
    expect(
      workflowDetail(
        row,
        {
          ...candidate,
          changes: {
            [proposal.change_id]: {
              ...change,
              status,
              proposal: null,
            },
          },
        },
        true,
      ).reviewId,
    ).toBeNull();
  },
);
it("requires the row's exact change to resolve", () => {
  expect(workflowDetail({ ...row, changeId: "missing" }, proposed(), true).reviewId).toBeNull();
});
