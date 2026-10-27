import { apply, parseEvent } from "./events";
import type { Transport, TransportStore } from "./events";

export interface EventStream {
  onopen?: ((event: Event) => void) | null;
  onmessage: ((message: MessageEvent<string>) => void) | null;
  onerror: ((event: Event) => void) | null;
  readonly readyState: number;
  close: () => void;
}
export interface LiveOptions {
  store: TransportStore;
  openStream?: (url: string) => EventStream;
  /** One failure per streak; `null` when a reopened stream is healthy again. */
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
  // The last failure was a lost connection, which a healthy reopen ends. A bad event does not
  // end that way: the reopened stream replays it, so only an applied event clears that failure.
  let disconnected = false;
  let badEvent = false;

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
      current.onopen = () => {
        if (stopped || current !== stream || !disconnected) return;
        // A server with nothing new to send applies no event, so the shell would keep its alert.
        disconnected = false;
        if (reported && !badEvent) {
          reported = false;
          onError?.(null);
        }
      };
      current.onmessage = (message) => {
        if (stopped || current !== stream) return;
        try {
          const event = parseEvent(message.data);
          if (!apply(store, event))
            throw new Error(`Live sequence or graph version mismatch at ${String(event.seq)}`);
          retryWindow = 500;
          reported = false;
          badEvent = false;
        } catch (error) {
          badEvent = true;
          retry(error);
        }
      };
      current.onerror = () => {
        if (stopped || current !== stream) return;
        // Own all retries, including CLOSED after a non-200 response. Closing CONNECTING
        // also prevents native Last-Event-ID from advancing past an unapplied event.
        disconnected = true;
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
