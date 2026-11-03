import { useEffect, useMemo, useSyncExternalStore } from "react";
import { stateStore } from "../state";
import type { ReplayPlayer } from "../transport";
import { createViewHistory } from "./viewHistory";

const noHistory = () => null;
const noSubscription = () => () => {};

/** App-owned navigation survives reducer replacement and is disposed with its replay source. */
export function useViewHistory(replay: ReplayPlayer | null) {
  const history = useMemo(
    () => (replay ? createViewHistory(replay, () => stateStore.getState().state) : null),
    [replay],
  );
  useEffect(() => () => history?.dispose(), [history]);
  const session = useSyncExternalStore(
    history?.subscribe ?? noSubscription,
    history?.getSnapshot ?? noHistory,
    noHistory,
  );
  return { history, session };
}
