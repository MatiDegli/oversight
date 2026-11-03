import { flowSteps, type PlanFlowRow, type State } from "../../state";

/** Presentation only: all state and gate evidence comes from the shared projection. */
export function workflowDetail(row: PlanFlowRow, state: State, hasReview: boolean) {
  const change = row.changeId ? state.changes[row.changeId] : undefined;
  const reviewId =
    hasReview && change?.proposal && ["ready_for_review", "conflict"].includes(change.status)
      ? row.changeId
      : null;
  return {
    task: row.task.task,
    word: row.word,
    condition: row.condition,
    wave: row.wave + 1,
    dependencies: row.task.depends_on.map((index) => `#${String(index + 1)}`),
    gates: row.steps.map((step, index) => ({ label: flowSteps[index], ...step })),
    progress: row.progress ?? "Not known",
    note: row.note ?? "Not known",
    agent: row.agentId ?? "Not known",
    role: "Not known",
    model: "Not known",
    mode: "Not known",
    reviewId,
  };
}
