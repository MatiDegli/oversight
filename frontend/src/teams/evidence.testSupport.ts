import type { State } from "../state";
import { applyEvent } from "../state";

/** Older renderer fixtures build status objects directly; retain them through the real reducer. */
export function agentEvidence(state: State): State {
  return Object.values(state.agents)
    .sort((a, b) => a.agent_id.localeCompare(b.agent_id))
    .reduce(
      (next, agent) =>
        applyEvent(next, {
          seq: next.lastSeq + 1,
          ts: next.clock ?? "2026-11-02T17:00:00Z",
          type: "agent.status",
          payload: agent,
        }),
      state,
    );
}
