import { stateStore } from "../state";
import { startAppTransport } from "../transport";

/** The shell owns the transport lifetime and exposes the applied cursor to screenshot clients. */
export function startAppSession(
  root: Pick<HTMLElement, "dataset"> | null,
  onError: (error: unknown) => void,
) {
  let active = true;
  const updateSeq = () => {
    if (root) root.dataset.seq = String(stateStore.getState().state.lastSeq);
  };
  updateSeq();
  const unsubscribe = stateStore.subscribe(updateSeq);
  const report = (error: unknown) => {
    if (active) onError(error);
  };
  const transport = startAppTransport(stateStore, report);
  void transport.ready.catch(report);
  return () => {
    active = false;
    unsubscribe();
    transport.stop();
  };
}
