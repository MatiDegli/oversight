import type { ServerEvent } from "@contracts";

type DecisionEventType =
  | "agent.status"
  | "change.proposed"
  | "change.facts_recorded"
  | "change.assessed"
  | "change.resolved"
  | "plan.updated"
  | "lead.status"
  | "revert.resolved"
  | "error";

/** Immutable evidence with its original envelope, including historical answers and delegations. */
export type DecisionEvent = Extract<ServerEvent, { type: DecisionEventType }>;

export function isDecisionEvent(event: ServerEvent): event is DecisionEvent {
  return (
    event.type === "agent.status" ||
    event.type === "change.proposed" ||
    event.type === "change.facts_recorded" ||
    event.type === "change.assessed" ||
    event.type === "change.resolved" ||
    event.type === "plan.updated" ||
    event.type === "lead.status" ||
    event.type === "revert.resolved" ||
    event.type === "error"
  );
}
