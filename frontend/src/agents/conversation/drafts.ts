import { createStore } from "zustand/vanilla";

/** Unsent messages live only in memory, separately for each run and agent. */
export const draftStore = createStore<Record<string, string>>(() => ({}));

export function draftKey(runId: string | null, agentId: string): string {
  return JSON.stringify([runId, agentId]);
}

export function setDraft(key: string, text: string): void {
  draftStore.setState((drafts) => {
    const next = { ...drafts };
    if (text) next[key] = text;
    else Reflect.deleteProperty(next, key);
    return next;
  }, true);
}

export function clearSentDraft(key: string, sent: string): void {
  // A response arriving after remount must not erase a newer draft.
  if (draftStore.getState()[key] === sent) setDraft(key, "");
}
