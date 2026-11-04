import type { ServerEvent } from "@contracts";

import { recordEventFact } from "./eventFacts";
import { applyThreadEvent } from "./applyThreadEvent";
import { applyAgentEvent } from "./applyAgentEvent";
import { applyUpdate, applyProposal, applyResolution, applyReview } from "./applyChangeEvent";
import { applyEvidenceEvent } from "./applyEvidenceEvent";
import { applyGraphEvent } from "./applyGraphEvent";
import { isDecisionEvent } from "./decisionProjection";
import { createInitialState } from "./types";
import type { State } from "./types";
import { foldStages } from "./stageFold";

/** Apply validated events, identically for live streams and parsed replay logs. */
export function applyEvent(state: State, event: ServerEvent): State {
  const next = applyEnvelope(state, event);
  if (next.gap) return next;
  const stageEvents = [...(event.type === "run.started" ? [] : state.stageEvents), event];
  let changes = next.changes;
  let changeStages = next.changeStages;
  for (const [id, change] of Object.entries(next.changes)) {
    const stages = foldStages(stageEvents, id);
    const revisionRounds =
      change.revisionRounds +
      Number(
        event.type === "change.resolved" &&
          event.payload.change_id === id &&
          event.payload.decision === "changes_requested",
      );
    // Open horizons can advance on any envelope without invalidating graph evidence.
    // Equal readings also keep the stage map itself stable on unrelated events.
    if (JSON.stringify(stages) !== JSON.stringify(changeStages[id])) {
      if (changeStages === next.changeStages) changeStages = { ...next.changeStages };
      changeStages[id] = stages;
    }
    if (revisionRounds !== change.revisionRounds) {
      if (changes === next.changes) changes = { ...next.changes };
      changes[id] = { ...change, revisionRounds };
    }
  }
  const observed = observeMain({ ...next, stageEvents, changeStages, changes }, event);
  return recordEventFact(observed, event);
}

function applyEnvelope(state: State, event: ServerEvent): State {
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
    case "preview.status":
      return next;
    case "lead.view":
    case "lead.reply":
    case "agent.message":
      return applyThreadEvent(state, next, event);
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

/** Rejected envelopes and graph-version gaps never become main evidence. */
function observeMain(state: State, event: ServerEvent): State {
  let commit: string | null = null;
  // Only these event kinds observe main; every other accepted envelope retains it.
  // eslint-disable-next-line @typescript-eslint/switch-exhaustiveness-check
  switch (event.type) {
    case "run.started":
      commit = event.payload.base_commit;
      break;
    case "graph.snapshot":
    case "graph.delta":
      commit = event.payload.commit;
      break;
    case "change.resolved":
      if (event.payload.merge === "merged") commit = event.payload.merge_commit;
      break;
    case "revert.resolved":
      commit = event.payload.main_commit;
      break;
    default:
      break;
  }
  return commit ? { ...state, mainHeads: [...state.mainHeads, { seq: event.seq, commit }] } : state;
}
