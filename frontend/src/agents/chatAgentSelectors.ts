import type { AgentStatus } from "@contracts";
import type { State } from "../state";
import { costText, matchesNeedsYou } from "../state";
import { selectAgentCard } from "./selectors";
import { selectPresentation } from "../teams/presentation";
export { slotUsage as chatAgentCount } from "./taskBoxModel";

const labels: Record<AgentStatus["state"], string> = {
  starting: "Starting",
  working: "Working",
  ready_for_review: "Ready for review",
  revising: "Revising",
  paused: "Paused",
  halted: "Halted",
  stopped: "Stopped",
  done: "Done",
};
/** Compatibility projection for K2a consumers; the shared presentation owns all allocation. */
export function teamIdentities(state: State) {
  return new Map(
    Object.entries(selectPresentation(state).byAgent).map(([id, identity]) => [
      id,
      { ...identity, letter: identity.label },
    ]),
  );
}

/** Row facts reuse the card's tool, model-wait, step-limit and cumulative cost projections. */
export function selectChatAgents(state: State) {
  return Object.entries(selectPresentation(state).byAgent)
    .sort(([, a], [, b]) => a.order - b.order || (a.subtask ?? 0) - (b.subtask ?? 0))
    .flatMap(([id, identity]) => {
      const card = selectAgentCard(state, id);
      if (!card) return [];
      const { agent } = card,
        change = state.changes[agent.change_id];
      const resolution = change?.resolution;
      const merged = resolution?.decision === "approved" && resolution.merge === "merged";
      const note =
        change && matchesNeedsYou(change.needsYou, card.proposal)
          ? change.needsYou?.reason
          : undefined;
      const label =
        agent.state === "working"
          ? card.activityLabel !== "Waiting" && !card.steps.length && state.focus[id]
            ? state.focus[id].mode === "edit"
              ? "Editing"
              : "Reading"
            : card.activityLabel
          : labels[agent.state];
      const activity = merged
        ? resolution.decided_by === "rules"
          ? "Merged by your rules"
          : "Merged"
        : agent.state === "ready_for_review"
          ? (note ?? "Waiting for your review")
          : agent.state === "working" || agent.state === "revising"
            ? card.activity
            : (agent.detail ?? labels[agent.state]);
      return [
        {
          ...identity,
          letter: identity.label,
          id,
          task: agent.task,
          model: card.modelLabel,
          label,
          activity,
          ready: card.readyForReview,
          done: agent.state === "done" || agent.state === "stopped",
          step: Math.min(card.step, card.maxSteps),
          maxSteps: card.maxSteps,
          cost:
            card.cost === undefined
              ? undefined
              : card.cost === null
                ? "Cost unknown"
                : costText(card.cost),
        },
      ];
    });
}
