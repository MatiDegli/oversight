import type { ServerEvent } from "@contracts";

import type { createStateStore } from "../state";

export type TransportStore = ReturnType<typeof createStateStore>;
export interface Transport {
  ready: Promise<void>;
  stop: () => void;
}

/** The server and fixture checks validate payload schemas; reject broken wire envelopes here. */
export function parseEvent(text: string): ServerEvent {
  const value: unknown = JSON.parse(text);
  if (typeof value !== "object" || value === null) throw new Error("Invalid event envelope");
  const event = value as Record<string, unknown>;
  const types: readonly unknown[] = [
    "run.started",
    "graph.snapshot",
    "graph.delta",
    "agent.status",
    "agent.step",
    "agent.focus",
    "change.updated",
    "change.proposed",
    "change.resolved",
    "change.assessed",
    "merge_mode.changed",
    "grant.created",
    "grant.revoked",
    "plan.updated",
    "change.reviewed",
    "change.facts_recorded",
    "tests.executed",
    "revert.resolved",
    "queue.updated",
    "lead.status",

    "usage.tick",
    "error",
  ];
  if (
    !Number.isSafeInteger(event.seq) ||
    typeof event.seq !== "number" ||
    event.seq < 1 ||
    typeof event.ts !== "string" ||
    !Number.isFinite(Date.parse(event.ts)) ||
    !types.includes(event.type) ||
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
