import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";

import fixture from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { createInitialState, stateStore } from "../state";
import type { EventStream } from "../transport";
import { App, startAppSession } from "./index";

// The app mounts the live diff, and Monaco needs a browser (pnpm test:browser).
vi.mock("../editor/monaco", () => ({ monaco: { editor: {} } }));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
  stateStore.setState({ state: createInitialState() });
});

it("App session owns root data-seq and clears the visible failure only on an applied event", async () => {
  vi.useFakeTimers();
  vi.spyOn(Math, "random").mockReturnValue(0);
  const sources: FakeSource[] = [];
  class FakeSource implements EventStream {
    onmessage: EventStream["onmessage"] = null;
    onerror: EventStream["onerror"] = null;
    readyState = 2;
    close = vi.fn();
    constructor(readonly url: string) {
      sources.push(this);
    }
    emit(data: string) {
      this.onmessage?.(new MessageEvent("message", { data }));
    }
  }
  const request = vi.fn();
  vi.stubGlobal("fetch", request);
  vi.stubGlobal("EventSource", FakeSource);
  const root: Pick<HTMLElement, "dataset"> = { dataset: {} };
  const onError = vi.fn();
  const stop = startAppSession(root, onError);
  try {
    expect(root.dataset.seq).toBe("0");
    const old = sources[0];
    if (!old) throw new Error("No EventSource");
    expect(old.url).toBe("/events?after=0");
    old.onerror?.(new Event("error"));
    expect(onError).toHaveBeenCalledWith(expect.any(Error));
    await vi.advanceTimersByTimeAsync(250);
    expect(onError).toHaveBeenCalledOnce();
    const source = sources[1];
    if (!source) throw new Error("No retry EventSource");
    for (const [index, event] of fixture.trim().split("\n").entries()) {
      source.emit(event);
      expect(root.dataset.seq).toBe(String(index + 1));
      expect(onError).toHaveBeenLastCalledWith(null);
      expect(renderToStaticMarkup(<App />)).not.toContain("data-seq");
    }
    expect(request).not.toHaveBeenCalled();
    stop();
    expect(source.close).toHaveBeenCalledOnce();
    const seq = root.dataset.seq;
    stateStore.setState({ state: createInitialState() });
    expect(root.dataset.seq).toBe(seq);
  } finally {
    stop();
  }
});
