import type { ServerEvent } from "@contracts";
import { afterEach, vi } from "vitest";
import fixture from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { createStateStore } from "../state";
import { parseEvent } from "./events";
import { startLive } from "./index";
import type { EventStream } from "./index";

const log = fixture.trim().split("\n").map(parseEvent);
const first = log[0];
const second = log[1];
if (first?.type !== "run.started" || second?.type !== "graph.snapshot") {
  throw new Error("Fixture must begin with run.started and graph.snapshot");
}
const run = first;
const graph = second;
class FakeSource implements EventStream {
  onopen: EventStream["onopen"] = null;
  onmessage: EventStream["onmessage"] = null;
  onerror: EventStream["onerror"] = null;
  readyState = 1;
  close = vi.fn();
  constructor(readonly url: string) {}
  open() {
    this.onopen?.(new Event("open"));
  }
  emit(event: ServerEvent) {
    this.raw(JSON.stringify(event));
  }
  raw(data: string) {
    this.onmessage?.(new MessageEvent("message", { data }));
  }
  fail(state = 2) {
    this.readyState = state;
    this.onerror?.(new Event("error"));
  }
}

const stops: (() => void)[] = [];
function setup(onOpen?: () => void) {
  const sources: FakeSource[] = [];
  const store = createStateStore();
  const onError = vi.fn();
  // The UI streams from seq 1; GET /graph is not on the connect path (proposal 4.6), so no
  // fetch of any kind may happen. Failing loudly here also catches a request sent by mistake.
  const request = vi.fn<typeof fetch>().mockRejectedValue(new Error("No network in this test"));
  vi.stubGlobal("fetch", request);
  const transport = startLive({
    store,
    onError,
    onOpen,
    openStream: (url) => {
      const source = new FakeSource(url);
      sources.push(source);
      return source;
    },
  });
  stops.push(transport.stop);
  const source = () => {
    const latest = sources.at(-1);
    if (!latest) throw new Error("Stream not connected");
    return latest;
  };
  return { store, request, transport, sources, source, onError };
}

afterEach(() => {
  for (const stop of stops.splice(0)) stop();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

export { log, run, graph, FakeSource, stops, setup };
