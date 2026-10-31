import type { ServerEvent } from "@contracts";

import { applyAgentEvent } from "./applyAgentEvent";
import { applyUpdate, applyProposal, applyResolution, applyReview } from "./applyChangeEvent";
import { applyEvidenceEvent } from "./applyEvidenceEvent";
import { applyGraphEvent } from "./applyGraphEvent";
import { isDecisionEvent } from "./decisionProjection";
import { createInitialState } from "./types";
import type { State } from "./types";

/** Apply validated events, identically for live streams and parsed replay logs. */
export function applyEvent(state: State, event: ServerEvent): State {
  // Only run.started identifies a run on the wire. A new run restarts its sequence at 1.
  const newRun = event.type === "run.started" && event.payload.run_id !== state.run?.run_id;
  if (event.seq !== (newRun ? 1 : state.lastSeq + 1)) return { ...state, gap: true };
  if (!state.run && event.type !== "run.started") return { ...state, gap: true };

  const next = {
    ...state,
    lastSeq: event.seq,
    gap: false,
    clock: event.ts,
    decisionEvents: isDecisionEvent(event)
      ? [...state.decisionEvents, event]
      : state.decisionEvents,
  };
  switch (event.type) {
    case "agent.message": // Line B's Conversation tab owns rendering.
      return next;
    case "grant.created":
    case "grant.revoked":
      return { ...next, grantEvents: [...state.grantEvents, event] };
    case "run.started":
      return {
        ...createInitialState(),
        run: event.payload,
        runStartedAt: event.ts,
        lastSeq: event.seq,
        clock: event.ts,
      };
    case "graph.snapshot":
    case "graph.delta":
      return applyGraphEvent(state, next, event);
    case "agent.status":
    case "model.attempt_failed":
    case "agent.step":
    case "agent.focus":
    case "usage.tick":
      return applyAgentEvent(state, next, event);
    case "change.updated":
      return applyUpdate(state, next, event);
    case "change.proposed":
      return applyProposal(state, next, event);
    case "change.resolved":
      return applyResolution(state, next, event);
    case "change.reviewed":
      return applyReview(state, next, event);
    case "tests.executed":
    case "change.facts_recorded":
    case "change.assessed":
    case "merge_mode.changed":
    case "plan.updated":
    case "revert.resolved":
    case "queue.updated":
    case "lead.status":
    case "error":
      return applyEvidenceEvent(state, next, event);
    default: {
      const exhaustive: never = event;
      return exhaustive;
    }
  }
}
