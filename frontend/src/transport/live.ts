import type { GraphResponse, ServerEvent } from "@contracts";

import { createInitialState } from "../state";
import { createCommandClient } from "./commands";
import { apply, parseEvent } from "./events";
import type { Transport, TransportStore } from "./events";

export interface EventStream {
  onmessage: ((message: MessageEvent<string>) => void) | null;
  close: () => void;
}
export interface LiveOptions {
  store: TransportStore;
  request?: typeof fetch;
  openStream?: (url: string) => EventStream;
  onError?: (error: unknown) => void;
}

export function startLive({
  store,
  request,
  openStream = (url) => new EventSource(url),
  onError,
}: LiveOptions): Transport {
  const client = createCommandClient(request);
  const controller = new AbortController();
  const isStopped = () => controller.signal.aborted;
  let stream: EventStream | undefined;
  let checkpoint: GraphResponse | undefined;
  let refreshing = false;

  function connect(run: string) {
    if (isStopped()) return;
    const params = new URLSearchParams({ after: String(store.getState().state.lastSeq), run });
    const current = openStream(`/events?${params}`);
    stream = current;
    current.onmessage = (message) => {
      if (isStopped() || current !== stream) return;
      try {
        let event: ServerEvent = parseEvent(message.data);
        if (event.type === "run.started" && event.payload.run_id !== run) {
          // The server deliberately starts a different run at 1, regardless of Last-Event-ID.
          checkpoint = undefined;
          if (!apply(store, event)) throw new Error("Invalid new-run sequence");
          void refresh().catch(report);
          return;
        }
        // A graph-only checkpoint cannot replace agent/change history. Replay the missing
        // events, using the authoritative graph for graph events covered by that checkpoint.
        if (
          checkpoint &&
          event.seq <= checkpoint.seq &&
          (event.type === "graph.delta" || event.type === "graph.snapshot")
        ) {
          event = {
            seq: event.seq,
            ts: event.ts,
            type: "graph.snapshot",
            payload: checkpoint.graph,
          };
        }
        if (!apply(store, event)) void refresh().catch(report);
        else if (event.type === "run.started" && checkpoint) {
          const graph = checkpoint.graph;
          store.setState(({ state }) => ({ state: { ...state, graph } }));
        }
      } catch (error) {
        report(error);
        void refresh().catch(report);
      }
    };
    // Do not close/recreate on error or open: EventSource owns normal reconnect and Last-Event-ID.
  }

  function report(error: unknown) {
    if (!isStopped()) onError?.(error);
  }

  async function refresh() {
    if (isStopped() || refreshing) return;
    refreshing = true;
    stream?.close();
    stream = undefined;
    try {
      const snapshot = await client.getGraph(controller.signal);
      if (isStopped()) return;
      checkpoint = snapshot;
      store.setState(({ state }) => ({
        state: {
          ...(state.run?.run_id === snapshot.run_id ? state : createInitialState()),
          graph: snapshot.graph,
          gap: false,
        },
      }));
      // Proposal 4.6 requires full history. GET /graph has no run metadata, agents or changes;
      // a fresh tab must start at 0, and a recovery must resume after the last applied event.
      connect(snapshot.run_id);
    } catch (error) {
      if (!isStopped()) {
        // A transient snapshot failure must not leave an existing session disconnected.
        const run = store.getState().state.run?.run_id;
        if (run) connect(run);
        throw error;
      }
    } finally {
      refreshing = false;
    }
  }

  return {
    ready: refresh(),
    stop: () => {
      stream?.close();
      stream = undefined;
      controller.abort();
    },
  };
}
