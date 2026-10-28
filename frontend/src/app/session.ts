import { stateStore } from "../state";
import { startAppTransport } from "../transport";
import { afterFirstGraphDraw } from "../graph";
import { prefetchEditorWhenIdle } from "../editor";

/** The shell owns the transport lifetime and exposes the applied cursor to screenshot clients. */
export function startAppSession(
  root: Pick<HTMLElement, "dataset"> | null,
  onError: (error: unknown) => void,
  onOpen?: () => void,
) {
  let active = true;
  let cancelPrefetch: () => void = () => undefined;
  const stopWaitingForGraph = afterFirstGraphDraw(() => {
    cancelPrefetch = prefetchEditorWhenIdle();
  });
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
  const transport = startAppTransport(stateStore, report, onOpen);
  void transport.ready.catch(report);
  return () => {
    active = false;
    stopWaitingForGraph();
    cancelPrefetch();
    unsubscribe();
    transport.stop();
  };
}
