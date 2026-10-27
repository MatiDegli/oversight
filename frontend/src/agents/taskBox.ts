import type { AgentStatus, Slot, StartAgent } from "@contracts";
import { createStore } from "zustand/vanilla";

import { selectSpend } from "../state";
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

export function slotHolder(state: State, slot: Slot): AgentStatus | undefined {
  return Object.values(state.agents).find(
    (agent) => agent.slot === slot && holdsSlot(state, agent),
  );
}

/** How many of the run's slots hold an agent, for the panel's "1 of 2 slots"; null before the run. */
export function slotUsage(state: State): { held: number; total: number } | null {
  if (!state.run) return null;
  const slots = state.run.slots.map((info) => info.slot);
  return { held: slots.filter((slot) => slotHolder(state, slot)).length, total: slots.length };
}

export const holderReason = (agent: AgentStatus) =>
  `Agent ${agent.slot} is ${holding[agent.state]}.`;

/** The server's first free, unbraked slot; if all free slots are braked, let it explain why. */
export function nextSlot(state: State): Slot | null {
  const free = (state.run?.slots ?? []).filter((info) => !slotHolder(state, info.slot));
  const blocked = selectSpend(state)?.brake === "blocked";
  return (
    free.find((info) => {
      const profile = state.run?.models.find((model) => model.id === info.default_model);
      return !blocked || profile?.provider !== "meta";
    })?.slot ??
    free[0]?.slot ??
    null
  );
}

export interface SlotChoice {
  slot: Slot;
  runId: string | undefined;
  /** Detect a slot taken even if its agent finished while the box was folded away. */
  agents: string[];
}

export function slotChoice(state: State, slot: Slot): SlotChoice {
  return { slot, runId: state.run?.run_id, agents: Object.keys(state.agents) };
}

export function selectedSlot(state: State, choice: SlotChoice | null): Slot | null {
  const taken =
    choice &&
    Object.values(state.agents).some(
      (agent) => agent.slot === choice.slot && !choice.agents.includes(agent.agent_id),
    );
  if (
    choice &&
    choice.runId === state.run?.run_id &&
    !taken &&
    state.run?.slots.some((info) => info.slot === choice.slot) &&
    !slotHolder(state, choice.slot)
  )
    return choice.slot;
  return nextSlot(state);
}

/**
 * Why no agent can start now, in one line, or null when a slot is free. The server decides in the
 * end (and answers slot_busy); this keeps the person from sending what it will refuse.
 */
export function startBlock(state: State, slot?: Slot | null): string | null {
  if (!state.run) return "Waiting for the server.";
  if (slot) {
    const holder = slotHolder(state, slot);
    return holder ? holderReason(holder) : null;
  }
  const slots = state.run.slots.map((info) => info.slot);
  if (slots.length === 0) return "The server offers no agent slot.";
  const held = Object.values(state.agents)
    .filter((agent) => slots.includes(agent.slot) && holdsSlot(state, agent))
    .sort((a, b) => a.slot.localeCompare(b.slot));
  const heldSlots = new Set(held.map((agent) => agent.slot));
  if (slots.some((slot) => !heldSlots.has(slot))) return null;
  return held.map(holderReason).join(" ");
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
  choice: SlotChoice | null;
  choose: (choice: SlotChoice) => void;
  setDraft: (text: string) => void;
  /** Sends the trimmed task and the selected slot when the box offers a choice. */
  start: (send: (body: StartAgent) => Promise<unknown>, slot?: Slot | null) => Promise<void>;
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
    choice: null,
    choose: (choice) => {
      if (!get().pending) set({ choice, failure: null });
    },
    setDraft: (draft) => {
      set({ draft, failure: null });
    },
    start: async (send, slot) => {
      const task = get().draft.trim();
      if (get().pending || task === "") return;
      set({ pending: true, failure: null });
      try {
        await send(slot ? { task, slot } : { task });
        // The agent's card arrives as events; the box only empties.
        set({ draft: "", pending: false });
      } catch (error) {
        set({ pending: false, failure: startFailure(error) });
      }
    },
  }));
}

export const taskBoxStore = createTaskBoxStore();
