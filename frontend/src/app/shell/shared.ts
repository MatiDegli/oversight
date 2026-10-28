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

// C-F are unreachable until the backend admits more than two agents.
// Line B many-agents look replaces this neutral placeholder before backend admission.
const unadmittedAgentLook = {
  tint: "bg-raised text-muted",
  border: "border-card-border",
  fill: "bg-muted",
} as const;

export const agentLook = {
  A: {
    tint: "bg-agent-a-tint text-agent-a-text",
    border: "border-agent-a",
    fill: "bg-agent-a",
  },
  B: {
    tint: "bg-agent-b-tint text-agent-b-text",
    border: "border-agent-b",
    fill: "bg-agent-b",
  },
  C: unadmittedAgentLook,
  D: unadmittedAgentLook,
  E: unadmittedAgentLook,
  F: unadmittedAgentLook,
} as const;

export const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";
