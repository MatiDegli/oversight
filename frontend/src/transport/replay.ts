import { createInitialState } from "../state";
import { apply, parseEvent } from "./events";
import type { Transport, TransportStore } from "./events";

export type ReplaySource = { text: string } | { url: string };
export interface ReplayOptions {
  store: TransportStore;
  source: ReplaySource;
  /** 1 follows recorded timing, 2 is twice as fast, 0 has no pauses. Gaps cap at 2 seconds. */
  speed?: number;
  request?: typeof fetch;
}

export function startReplay({
  store,
  source,
  speed = 1,
  request = fetch,
}: ReplayOptions): Transport {
  if (!Number.isFinite(speed) || speed < 0)
    throw new Error("Replay speed must be finite and nonnegative");
  const controller = new AbortController();
  const isStopped = () => controller.signal.aborted;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let wake: (() => void) | undefined;

  async function play() {
    let text: string;
    if ("text" in source) text = source.text;
    else {
      const response = await request(source.url, { signal: controller.signal });
      if (!response.ok) throw new Error(`Replay log request failed: ${String(response.status)}`);
      text = await response.text();
    }
    if (isStopped()) return;
    const events = text
      .split(/\r?\n/)
      .filter((line) => line.trim())
      .map(parseEvent);
    if (events[0]?.type !== "run.started") throw new Error("Replay must start with run.started");
    store.setState({ state: createInitialState() });
    let previous: number | undefined;
    for (const event of events) {
      const moment = Date.parse(event.ts);
      const delay =
        previous === undefined || speed === 0
          ? 0
          : Math.min(Math.max(moment - previous, 0) / speed, 2000);
      if (delay > 0)
        await new Promise<void>((resolve) => {
          wake = resolve;
          timer = setTimeout(resolve, delay);
        });
      if (isStopped()) return;
      if (!apply(store, event))
        throw new Error(`Replay sequence or graph version mismatch at ${String(event.seq)}`);
      previous = moment;
    }
  }

  return {
    ready: play().catch((error: unknown) => {
      if (!isStopped()) throw error;
    }),
    stop: () => {
      controller.abort();
      clearTimeout(timer);
      wake?.();
    },
  };
}
