import { stateStore } from "../state";
import { startAppTransport } from "../transport";

/** The shell owns the transport lifetime and exposes the applied cursor to screenshot clients. */
export function startAppSession(
  root: Pick<HTMLElement, "dataset"> | null,
  onError: (error: unknown) => void,
) {
  let active = true;
  if (root) root.dataset.seq = String(stateStore.getState().state.lastSeq);
  const unsubscribe = stateStore.subscribe(({ state }, previous) => {
    if (root) root.dataset.seq = String(state.lastSeq);
    if (
      !state.gap &&
      (state.lastSeq !== previous.state.lastSeq || state.run !== previous.state.run)
    )
      onError(null);
  });
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
