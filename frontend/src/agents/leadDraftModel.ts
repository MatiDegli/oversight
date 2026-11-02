import type { StartLead } from "@contracts";
import { createStore } from "zustand/vanilla";
import type { State } from "../state";
import { CommandError } from "../transport";
import { taskBoxStore } from "./taskBoxModel";

export function selectedLeadModel(state: State, choice: { runId?: string; id: string } | null) {
  const models = state.run?.models ?? [];
  const chosen = choice?.runId === state.run?.run_id ? choice?.id : undefined;
  const id = chosen ?? state.run?.slots.find((slot) => slot.slot === "A")?.default_model;
  return models.find((model) => model.id === id) ?? null;
}

/** Kept outside the form so Cancel, navigation and pending answers preserve the draft. */
export function createLeadDraftStore() {
  return createStore<{
    draft: string;
    pending: boolean;
    failure: string | null;
    choice: { runId?: string; id: string } | null;
    setDraft: (draft: string) => void;
    choose: (id: string, runId?: string) => void;
    moveToTask: () => void;
    start: (send: (body: StartLead) => Promise<unknown>, model: string) => Promise<boolean>;
  }>()((set, get) => ({
    draft: "",
    pending: false,
    failure: null,
    choice: null,
    setDraft: (draft) => {
      if (!get().pending) set({ draft, failure: null });
    },
    choose: (id, runId) => {
      if (!get().pending) set({ choice: { id, runId }, failure: null });
    },
    moveToTask: () => {
      const task = taskBoxStore.getState();
      if (task.draft === "" && !task.pending && !get().pending) {
        task.setDraft(get().draft);
        set({ draft: "", failure: null });
      }
    },
    start: async (send, model) => {
      const task = get().draft.trim();
      if (get().pending || !task || !model) return false;
      set({ pending: true, failure: null });
      try {
        await send({ task, model });
        set({ draft: "", pending: false });
        return true;
      } catch (error) {
        set({
          pending: false,
          failure:
            error instanceof CommandError
              ? error.status >= 500
                ? `The server failed to start the lead: ${error.message}`
                : error.message
              : "The request did not reach the server. Check the connection and try again.",
        });
        return false;
      }
    },
  }));
}
export const leadDraftStore = createLeadDraftStore();
