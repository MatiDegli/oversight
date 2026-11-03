import type { State } from "../state";
import type { ReplayPlayer } from "../transport";

export interface ViewHistorySession {
  eventSeq: number;
  position: number;
  lastSeq: number;
  chat: State;
  revision: number;
}

/** Lives outside the reducer store that seeks replace. Ordinary playback cancels the override. */
export function createViewHistory(player: ReplayPlayer, getState: () => State) {
  let session: ViewHistorySession | null = null;
  const listeners = new Set<() => void>();
  const publish = (next: ViewHistorySession | null) => {
    session = next;
    listeners.forEach((listener) => {
      listener();
    });
  };
  let unsubscribe: (() => void) | undefined;
  const synchronize = () => {
    const snapshot = player.getSnapshot();
    if (session && (snapshot.revision !== session.revision || snapshot.status === "playing"))
      publish(null);
  };
  return {
    getSnapshot: () => session,
    subscribe: (listener: () => void) => {
      if (!unsubscribe) unsubscribe = player.subscribe(synchronize);
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
        if (!listeners.size) {
          unsubscribe?.();
          unsubscribe = undefined;
        }
      };
    },
    open: (eventSeq: number) => {
      player.pause();
      if (!player.getSnapshot().events.some((event) => event.seq === eventSeq)) return;
      const saved = session ?? {
        position: player.getSnapshot().position,
        lastSeq: getState().lastSeq,
        chat: getState(),
      };
      const revision = player.getSnapshot().revision + 1;
      // Publish Chat before the reducer replacement: observers must never reconcile a prefix
      // without its saved conversation. The controller publishes the matching revision next.
      publish({ ...saved, eventSeq, revision });
      player.seekSeq(eventSeq);
      if (player.getSnapshot().revision !== revision) publish(null);
    },
    back: () => {
      if (!session) return;
      const saved = session;
      publish({ ...saved, revision: player.getSnapshot().revision + 1 });
      player.seekSeq(saved.lastSeq, saved.position);
      publish(null);
    },
    dispose: () => {
      unsubscribe?.();
      unsubscribe = undefined;
      publish(null);
      listeners.clear();
    },
  };
}
