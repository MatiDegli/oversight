import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";

import fixture from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { App, startAppSession } from "../app";
import { createInitialState, stateStore } from "../state";
import { parseEvent } from "./events";
import type { EventStream } from "./index";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  stateStore.setState({ state: createInitialState() });
});

it("App session starts live transport and data-seq follows applied events on the root", async () => {
  const events = fixture.trim().split("\n").map(parseEvent);
  const run = events[0];
  const graph = events[1];
  if (run?.type !== "run.started" || graph?.type !== "graph.snapshot")
    throw new Error("Invalid fixture");
  const sources: FakeSource[] = [];
  class FakeSource implements EventStream {
    onmessage: EventStream["onmessage"] = null;
    close = vi.fn();
    constructor(readonly url: string) {
      sources.push(this);
    }
    emit(event: string) {
      this.onmessage?.(new MessageEvent("message", { data: event }));
    }
  }
  const request = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json({ run_id: run.payload.run_id, seq: 2, graph: graph.payload }));
  vi.stubGlobal("fetch", request);
  vi.stubGlobal("EventSource", FakeSource);
  const root: Pick<HTMLElement, "dataset"> = { dataset: {} };
  const onError = vi.fn();
  const stop = startAppSession(root, onError);
  try {
    expect(root.dataset.seq).toBe("0");
    await vi.waitFor(() => {
      expect(sources).toHaveLength(1);
    });
    const source = sources[0];
    if (!source) throw new Error("No EventSource");
    expect(source.url).toBe(`/events?after=0&run=${run.payload.run_id}`);
    // SSR uses getInitialState; point it to each applied state to inspect the shell output.
    vi.spyOn(stateStore, "getInitialState").mockImplementation(stateStore.getState);
    for (const event of events) {
      source.emit(JSON.stringify(event));
      expect(root.dataset.seq).toBe(String(event.seq));
      expect(renderToStaticMarkup(<App />)).toContain(`data-seq="${String(event.seq)}"`);
    }
    expect(onError).not.toHaveBeenCalled();
    stop();
    expect(source.close).toHaveBeenCalledOnce();
    stateStore.setState({ state: createInitialState() });
    expect(root.dataset.seq).toBe(String(events.at(-1)?.seq));
  } finally {
    stop();
  }
});
