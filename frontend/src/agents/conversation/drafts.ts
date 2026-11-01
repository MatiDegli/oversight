import { createStore } from "zustand/vanilla";

interface Draft {
  text: string;
  pending: boolean;
}

/** Unsent messages and in-flight sends live only in memory, per run and agent. */
export const draftStore = createStore<Record<string, Draft>>(() => ({}));

export function draftKey(runId: string | null, agentId: string): string {
  return JSON.stringify([runId, agentId]);
}

function updateDraft(key: string, patch: Partial<Draft>): void {
  draftStore.setState((drafts) => {
    const draft = { text: "", pending: false, ...drafts[key], ...patch };
    const next = { ...drafts };
    if (draft.text || draft.pending) next[key] = draft;
    else Reflect.deleteProperty(next, key);
    return next;
  }, true);
}

export function setDraft(key: string, text: string): void {
  updateDraft(key, { text });
}

/** Claim the key synchronously, including before subscribed components re-render. */
export function beginSend(key: string): boolean {
  if (draftStore.getState()[key]?.pending) return false;
  updateDraft(key, { pending: true });
  return true;
}

export function finishSend(key: string): void {
  updateDraft(key, { pending: false });
}

export function clearSentDraft(key: string, sent: string): void {
  // A response arriving after remount must not erase a newer draft.
  if (draftStore.getState()[key]?.text === sent) setDraft(key, "");
}
