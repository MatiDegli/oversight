import { apply, parseEvent } from "./events";
import type { Transport, TransportStore } from "./events";

export interface EventStream {
  onmessage: ((message: MessageEvent<string>) => void) | null;
  onerror: ((event: Event) => void) | null;
  readonly readyState: number;
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
  openStream = (url) => new EventSource(url),
  onError,
}: LiveOptions): Transport {
  let stopped = false;
  let stream: EventStream | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let retryWindow = 500;
  let reported = false;

  function retry(error: unknown) {
    stream?.close();
    stream = undefined;
    // Equal jitter: a positive wait on every retry, capped at 30 seconds.
    const delay = retryWindow * (0.5 + Math.random() * 0.5);
    retryWindow = Math.min(retryWindow * 2, 30_000);
    timer = setTimeout(connect, delay);
    if (!reported) {
      reported = true;
      onError?.(error);
    }
  }

  function connect() {
    timer = undefined;
    if (stopped) return;
    const state = store.getState().state;
    const params = new URLSearchParams({ after: String(state.lastSeq) });
    if (state.run) params.set("run", state.run.run_id);
    try {
      const current = openStream(`/events?${params}`);
      stream = current;
      current.onmessage = (message) => {
        if (stopped || current !== stream) return;
        try {
          const event = parseEvent(message.data);
          if (!apply(store, event))
            throw new Error(`Live sequence or graph version mismatch at ${String(event.seq)}`);
          retryWindow = 500;
          reported = false;
        } catch (error) {
          retry(error);
        }
      };
      current.onerror = () => {
        if (stopped || current !== stream) return;
        // Own all retries, including CLOSED after a non-200 response. Closing CONNECTING
        // also prevents native Last-Event-ID from advancing past an unapplied event.
        retry(
          new Error(`Live event stream disconnected (${String(current.readyState)}); retrying`),
        );
      };
    } catch (error) {
      retry(error);
    }
  }

  connect();
  return {
    ready: Promise.resolve(),
    stop: () => {
      stopped = true;
      clearTimeout(timer);
      stream?.close();
      stream = undefined;
    },
  };
}
