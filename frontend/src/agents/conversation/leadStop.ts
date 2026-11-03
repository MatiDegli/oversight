import type { StopLead } from "@contracts";
import { createStore } from "zustand/vanilla";
import { stateStore } from "../../state";
import { commandFailure } from "../../transport";
import { selectLeadStatuses } from "../chatLeadSelectors";
import { draftKey, setDraftFailure } from "./drafts";

export const leadStopStore = createStore<Record<string, boolean>>(() => ({}));
export const leadEnded = (state: string) => ["stopped", "halted", "done"].includes(state);

// Admission is not finalization: only authoritative terminal status releases a pending Stop.
stateStore.subscribe(({ state }) => {
  const statuses = selectLeadStatuses(state);
  const next = { ...leadStopStore.getState() };
  for (const key of Object.keys(next)) {
    const [runId, id] = JSON.parse(key) as [string | null, string];
    const status = statuses.get(id);
    if (runId !== (state.run?.run_id ?? null) || (status && leadEnded(status.state)))
      Reflect.deleteProperty(next, key);
  }
  leadStopStore.setState(next, true);
});

export async function requestLeadStop(
  runId: string | null,
  id: string,
  revision: number,
  send: (id: string, body: StopLead) => Promise<unknown>,
) {
  const key = draftKey(runId, id);
  if (stateStore.getState().state.run?.run_id !== runId || leadStopStore.getState()[key]) return;
  leadStopStore.setState({ [key]: true });
  setDraftFailure(key, undefined);
  try {
    await send(id, { revision });
  } catch (error) {
    if (!leadStopStore.getState()[key]) return;
    const next = { ...leadStopStore.getState() };
    Reflect.deleteProperty(next, key);
    leadStopStore.setState(next, true);
    setDraftFailure(key, commandFailure("Stop lead", error));
  }
}
