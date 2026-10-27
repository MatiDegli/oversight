import type { AgentStatus, StartAgent } from "@contracts";
import { createStore } from "zustand/vanilla";

import type { State } from "../state";
import { CommandError } from "../transport";

/** What an agent that still holds its slot is doing, in the words of the one-line reason. */
const holding: Record<AgentStatus["state"], string> = {
  starting: "starting",
  working: "working",
  ready_for_review: "waiting for your review",
  revising: "revising its change",
  halted: "halted and waiting for your review",
  paused: "paused",
  stopped: "stopping",
  done: "finishing up",
};

/**
 * Whether an agent still holds its slot. The server frees a slot only once the change is resolved
 * (rejected, or merged), so a conflict, a revision and a halted run still hold it. Without the
 * change in the state, the agent's own end state decides.
 */
function holdsSlot(state: State, agent: AgentStatus): boolean {
  const change = state.changes[agent.change_id];
  if (change) return change.status !== "approved" && change.status !== "rejected";
  return agent.state !== "done" && agent.state !== "stopped";
}

/**
 * Why no agent can start now, in one line, or null when a slot is free. The server decides in the
 * end (and answers slot_busy); this keeps the person from sending what it will refuse.
 */
export function startBlock(state: State): string | null {
  if (!state.run) return "Waiting for the server.";
  const slots = state.run.slots.map((info) => info.slot);
  if (slots.length === 0) return "The server offers no agent slot.";
  const held = Object.values(state.agents)
    .filter((agent) => slots.includes(agent.slot) && holdsSlot(state, agent))
    .sort((a, b) => a.slot.localeCompare(b.slot));
  const heldSlots = new Set(held.map((agent) => agent.slot));
  if (slots.some((slot) => !heldSlots.has(slot))) return null;
  return held.map((agent) => `Agent ${agent.slot} is ${holding[agent.state]}.`).join(" ");
}

/** What the person reads under the box when a start fails. */
export function startFailure(error: unknown): string {
  if (error instanceof CommandError) {
    // A 4xx names its own cause (spend_brake, slot_busy, invalid_request); so does a 500.
    return error.status >= 500
      ? `The server failed to start the agent: ${error.message}`
      : error.message;
  }
  return "The request did not reach the server. Check the connection and try again.";
}

export interface TaskBoxModel {
  draft: string;
  /** From the request's departure until the server's answer. */
  pending: boolean;
  /** The last start's failure; the next edit or start clears it. */
  failure: string | null;
  setDraft: (text: string) => void;
  /** Sends the trimmed task, and nothing else, once; a blank or pending box sends nothing. */
  start: (send: (body: StartAgent) => Promise<unknown>) => Promise<void>;
}

/**
 * The box's draft and request, outside the component so that folding the agents panel (which
 * unmounts the box) loses neither the text nor an answer still on its way.
 */
export function createTaskBoxStore() {
  return createStore<TaskBoxModel>()((set, get) => ({
    draft: "",
    pending: false,
    failure: null,
    setDraft: (draft) => {
      set({ draft, failure: null });
    },
    start: async (send) => {
      const task = get().draft.trim();
      if (get().pending || task === "") return;
      set({ pending: true, failure: null });
      try {
        await send({ task });
        // The agent's card arrives as events; the box only empties.
        set({ draft: "", pending: false });
      } catch (error) {
        set({ pending: false, failure: startFailure(error) });
      }
    },
  }));
}

export const taskBoxStore = createTaskBoxStore();
