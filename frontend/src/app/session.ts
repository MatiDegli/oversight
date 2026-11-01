import type { ReplayPlayer } from "../transport";
import { stateStore } from "../state";
import { startAppTransport } from "../transport";
// eslint-disable-next-line no-restricted-imports -- C1 uses a pure leaf to isolate graph startup.
import { afterFirstGraphDraw } from "../graph/firstDraw";
import { prefetchEditorWhenIdle } from "../editor";

/** The shell owns the transport lifetime and exposes the applied cursor to screenshot clients. */
export function startAppSession(
  root: Pick<HTMLElement, "dataset"> | null,
  onError: (error: unknown) => void,
  onOpen?: () => void,
  onReplay?: (player: ReplayPlayer) => void,
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
  // Revoke write capability before stopping: even a queued obsolete callback is harmless.
  const guarded: typeof stateStore = {
    ...stateStore,
    setState(next) {
      if (active) stateStore.setState(next);
    },
    getState() {
      const current = stateStore.getState();
      return {
        ...current,
        applyEvents(events) {
          if (active) current.applyEvents(events);
        },
      };
    },
  };
  const transport = startAppTransport(
    guarded,
    report,
    () => {
      if (active) onOpen?.();
    },
    (player) => {
      if (active) onReplay?.(player);
    },
  );
  void transport.ready.catch(report);
  return () => {
    active = false;
    stopWaitingForGraph();
    cancelPrefetch();
    unsubscribe();
    transport.stop();
  };
}
