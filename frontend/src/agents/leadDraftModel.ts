import type { StartLead } from "@contracts";
import { createStore } from "zustand/vanilla";
import { stateStore } from "../state";
import { leadCapacity } from "./chatLeadSelectors";
import { CommandError } from "../transport";
import { taskBoxStore } from "./taskBoxModel";
import { setupFailure } from "./setupFailure";

/** Kept outside the form so Cancel, navigation and pending answers preserve the draft. */
export function createLeadDraftStore() {
  return createStore<{
    draft: string;
    pending: boolean;
    failure: string | null;
    setDraft: (draft: string) => void;
    moveToTask: () => void;
    start: (send: (body: StartLead) => Promise<unknown>) => Promise<boolean>;
  }>()((set, get) => ({
    draft: "",
    pending: false,
    failure: null,
    setDraft: (draft) => {
      if (!get().pending) set({ draft, failure: null });
    },
    moveToTask: () => {
      const task = taskBoxStore.getState();
      if (task.draft === "" && !task.pending && !get().pending) {
        task.setDraft(get().draft);
        set({ draft: "", failure: null });
      }
    },
    start: async (send) => {
      const task = get().draft.trim();
      if (get().pending || !task || leadCapacity(stateStore.getState().state).atCap) return false;
      set({ pending: true, failure: null });
      try {
        await send({ task });
        set({ draft: "", pending: false });
        return true;
      } catch (error) {
        set({
          pending: false,
          failure:
            error instanceof CommandError
              ? error.code === "not_configured"
                ? setupFailure(error)
                : error.status >= 500
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
