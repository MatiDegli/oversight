import type { ServerEvent } from "@contracts";

import type { createStateStore } from "../state";

export type TransportStore = ReturnType<typeof createStateStore>;
export interface Transport {
  ready: Promise<void>;
  stop: () => void;
}

const eventTypes: Record<ServerEvent["type"], true> = {
  "run.started": true,
  "graph.snapshot": true,
  "graph.delta": true,
  "agent.status": true,
  "agent.step": true,
  "agent.message": true,
  "agent.focus": true,
  "change.updated": true,
  "change.proposed": true,
  "change.resolved": true,
  "change.assessed": true,
  "merge_mode.changed": true,
  "grant.created": true,
  "grant.revoked": true,
  "plan.updated": true,
  "change.reviewed": true,
  "change.facts_recorded": true,
  "tests.executed": true,
  "revert.resolved": true,
  "queue.updated": true,
  "lead.status": true,
  "lead.reply": true,
  "usage.tick": true,
  "model.attempt_failed": true,
  error: true,
};

/** The server and fixture checks validate payload schemas; reject broken wire envelopes here. */
export function parseEvent(text: string): ServerEvent {
  const value: unknown = JSON.parse(text);
  if (typeof value !== "object" || value === null) throw new Error("Invalid event envelope");
  const event = value as Record<string, unknown>;
  if (
    !Number.isSafeInteger(event.seq) ||
    typeof event.seq !== "number" ||
    event.seq < 1 ||
    typeof event.ts !== "string" ||
    !Number.isFinite(Date.parse(event.ts)) ||
    typeof event.type !== "string" ||
    !Object.hasOwn(eventTypes, event.type) ||
    typeof event.payload !== "object" ||
    event.payload === null
  )
    throw new Error("Invalid event envelope");
  return value as ServerEvent;
}

export function apply(store: TransportStore, event: ServerEvent) {
  store.getState().applyEvents([event]);
  return !store.getState().state.gap;
}
