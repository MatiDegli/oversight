export { agentLook } from "../../theme";
import type { AgentStatus } from "@contracts";
type AgentState = AgentStatus["state"];

/** The card's words for a state; the agents rail uses the one-word caption. */
export const stateWords: Record<AgentState, [label: string, caption: string]> = {
  starting: ["Starting", "Starting"],
  working: ["Working", "Working"],
  ready_for_review: ["Ready for review", "Ready"],
  revising: ["Revising", "Revising"],
  halted: ["Halted", "Halted"],
  stopped: ["Stopped", "Stopped"],
  done: ["Done", "Done"],
  paused: ["Paused", "Paused"],
};

export const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";
