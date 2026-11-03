import type { ServerEvent } from "@contracts";
import type { EventFact, State } from "./types";

/** Metadata only; called after application so a rejected envelope leaves no fact. */
export function recordEventFact(state: State, event: ServerEvent): State {
  const payload = event.payload;
  const agentId = "agent_id" in payload ? payload.agent_id : null;
  const fact: EventFact = { type: event.type, ts: event.ts, agentId };
  return { ...state, eventFacts: { ...state.eventFacts, [event.seq]: fact } };
}
