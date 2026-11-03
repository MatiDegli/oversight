import type { ServerEvent } from "@contracts";
import type { State } from "./types";

type ThreadEvent = Extract<ServerEvent, { type: "lead.view" | "lead.reply" | "agent.message" }>;

/** Ordered thread records share the same fold in live mode and replay. */
export function applyThreadEvent(state: State, next: State, event: ThreadEvent): State {
  switch (event.type) {
    case "lead.view":
      return {
        ...next,
        leadViews: {
          ...state.leadViews,
          [event.payload.agent_id]: [...(state.leadViews[event.payload.agent_id] ?? []), event],
        },
      };
    case "lead.reply":
      return {
        ...next,
        leadReplies: {
          ...state.leadReplies,
          [event.payload.agent_id]: [...(state.leadReplies[event.payload.agent_id] ?? []), event],
        },
      };
    case "agent.message":
      return {
        ...next,
        messages: {
          ...state.messages,
          [event.payload.agent_id]: [
            ...(state.messages[event.payload.agent_id] ?? []),
            { seq: event.seq, ts: event.ts, text: event.payload.text },
          ],
        },
      };
  }
}
