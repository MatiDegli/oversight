import type { AgentStatus, EnqueueTask, ErrorCode, QueuedTask, Slot, StartAgent } from "@contracts";
import { createStore } from "zustand/vanilla";

import { selectSpend } from "../state";
import type { State } from "../state";
import { teamIdentities } from "./chatAgentSelectors";
import { CommandError } from "../transport";
import { setupFailure } from "./setupFailure";

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

export const holderReason = (state: State, agent: AgentStatus) =>
  `Agent ${teamIdentities(state).get(agent.agent_id)?.letter ?? agent.slot} is ${holding[agent.state]}.`;

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

/** The models a start may name: each slot's profile, once, with its label. */
export function offeredModels(state: State): { id: string; label: string }[] {
  const ids = [...new Set(state.run?.slots.map((info) => info.default_model))];
  return ids.map((id) => ({
    id,
    label: state.run?.models.find((model) => model.id === id)?.label ?? id,
  }));
}

export interface ModelPick {
  model: string;
  runId: string | undefined;
}

/** The person's model for the next start, while this run still offers it; null means the slot's. */
export function pickedModel(state: State, pick: ModelPick | null): string | null {
  if (!pick || pick.runId !== state.run?.run_id) return null;
  return offeredModels(state).some((model) => model.id === pick.model) ? pick.model : null;
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
    return holder ? holderReason(state, holder) : null;
  }
  const slots = state.run.slots.map((info) => info.slot);
  if (slots.length === 0) return "The server offers no agent slot.";
  const held = Object.values(state.agents)
    .filter((agent) => slots.includes(agent.slot) && holdsSlot(state, agent))
    .sort((a, b) => a.slot.localeCompare(b.slot));
  const heldSlots = new Set(held.map((agent) => agent.slot));
  if (slots.some((slot) => !heldSlots.has(slot))) return null;
  return held.map((agent) => holderReason(state, agent)).join(" ");
}

/**
 * Tasks an agent can finish in the demo shop as it ships, each named by a short label. The first is
 * the recorded run's task; each fixes a gap the shop's code and tests still have.
 */
const demoExamples = [
  {
    label: "Reject negative prices",
    task: "compute_price in shop/pricing.py accepts a negative unit_price or quantity and returns a negative price. Make it raise ValueError for a negative unit_price, a negative quantity, or an extra_percent outside 0 to 100, with a short message naming the argument. Add tests for each case next to the existing compute_price tests, and keep the existing tests passing.",
  },
  {
    label: "Validate reserve quantities",
    task: "Inventory.reserve in shop/inventory.py accepts a zero or negative quantity, and a negative one increases what is available. Make it raise ValueError for a quantity that is not positive, as restock and release already do, and add tests next to the existing reserve tests; keep the existing tests passing.",
  },
  {
    label: "Leave cancelled orders out of revenue",
    task: "revenue_by_category in shop/orders.py still counts cancelled orders. Make it skip orders whose cancelled flag is set, add a test next to the existing revenue_by_category test, and keep the existing tests passing.",
  },
];

/** Example tasks for a first start: only for the demo shop, and only before any agent exists. */
export function exampleTasks(state: State): { label: string; task: string }[] {
  if (state.run?.workspace !== "demo-repo" || Object.keys(state.agents).length > 0) return [];
  return demoExamples;
}

/** The person's own tasks still waiting for a slot, in queue order; a lead's are its own. */
export function selectQueuedTasks(state: State): QueuedTask[] {
  const latest = new Map<string, QueuedTask>();
  for (const event of state.decisionEvents) {
    if (event.type !== "queue.updated") continue;
    const item = event.payload;
    const id = item.queued_task_id;
    if ((latest.get(id)?.revision ?? 0) < item.revision) latest.set(id, item);
  }
  return [...latest.values()].filter(
    (item) =>
      item.agent &&
      !item.started_by &&
      (item.state === "queued" || item.state === "blocked" || item.state === "starting"),
  );
}

/** What the person reads under the box when a start fails. */
export function startFailure(error: unknown, action = "start the agent"): string {
  if (error instanceof CommandError) {
    if (error.code === "not_configured") return setupFailure(error);
    // A 4xx names its own cause (spend_brake, slot_busy, invalid_request); so does a 500.
    return error.status >= 500 ? `The server failed to ${action}: ${error.message}` : error.message;
  }
  return "The request did not reach the server. Check the connection and try again.";
}

export interface TaskBoxModel {
  draft: string;
  /** The action that supplied this draft, retained while file actions unmount. */
  prefilled: { nodeId: string; text: string } | null;
  /** From the request's departure until the server's answer. */
  pending: boolean;
  /** Whether the pending request queues the task rather than starting it. */
  queueing: boolean;
  /** The last queueing's confirmation; the next edit or request clears it. */
  notice: string | null;
  /** The last start's failure; the next edit or start clears it. */
  failure: string | null;
  failureCode: ErrorCode | null;
  choice: SlotChoice | null;
  choose: (choice: SlotChoice) => void;
  /** The model the person picked from the chip; null keeps each slot's own. */
  model: ModelPick | null;
  pickModel: (pick: ModelPick) => void;
  setDraft: (text: string) => void;
  /** Fill only an empty, idle box; even whitespace belongs to the person. */
  prefill: (text: string, nodeId: string) => boolean;
  /** Sends the trimmed task, the selected slot when the box offers a choice, and a picked model. */
  start: (
    send: (body: StartAgent) => Promise<unknown>,
    slot?: Slot | null,
    model?: string | null,
  ) => Promise<void>;
  /** Queues the trimmed task for the next free slot, or the selected one when the box offers a choice. */
  queue: (
    send: (body: EnqueueTask) => Promise<unknown>,
    slot?: Slot | null,
    model?: string | null,
  ) => Promise<void>;
}

/**
 * The box's draft and request, outside the component so that folding the agents panel (which
 * unmounts the box) loses neither the text nor an answer still on its way.
 */
export function createTaskBoxStore() {
  return createStore<TaskBoxModel>()((set, get) => ({
    draft: "",
    prefilled: null,
    pending: false,
    queueing: false,
    notice: null,
    failure: null,
    failureCode: null,
    choice: null,
    choose: (choice) => {
      if (!get().pending) set({ choice, failure: null, failureCode: null });
    },
    model: null,
    pickModel: (model) => {
      if (!get().pending) set({ model, failure: null, failureCode: null });
    },
    setDraft: (draft) => {
      set({ draft, prefilled: null, failure: null, failureCode: null, notice: null });
    },
    prefill: (draft, nodeId) => {
      if (get().pending || get().draft !== "") return false;
      set({ draft, prefilled: { nodeId, text: draft }, failure: null, failureCode: null });
      return true;
    },
    start: async (send, slot, model) => {
      const task = get().draft.trim();
      if (get().pending || task === "") return;
      set({ pending: true, failure: null, failureCode: null, notice: null });
      try {
        await send({ task, ...(slot ? { slot } : {}), ...(model ? { model } : {}) });
        // The agent's card arrives as events; the box only empties.
        set({ draft: "", prefilled: null, pending: false });
      } catch (error) {
        set({
          pending: false,
          failure: startFailure(error),
          failureCode: error instanceof CommandError ? (error.code ?? null) : null,
        });
      }
    },
    queue: async (send, slot, model) => {
      const task = get().draft.trim();
      if (get().pending || task === "") return;
      set({ pending: true, queueing: true, failure: null, failureCode: null, notice: null });
      try {
        await send({ agent: { task, slot: slot ?? null, model: model ?? null, zone: null } });
        // The server starts it when a slot frees; queue.updated events list it meanwhile.
        set({
          draft: "",
          prefilled: null,
          pending: false,
          queueing: false,
          notice: "Queued. It starts when a slot is free.",
        });
      } catch (error) {
        set({
          pending: false,
          queueing: false,
          failure: startFailure(error, "queue the task"),
          failureCode: error instanceof CommandError ? (error.code ?? null) : null,
        });
      }
    },
  }));
}

export const taskBoxStore = createTaskBoxStore();
