import type { GraphResponse, GraphSnapshotEvent, ServerEvent } from "@contracts";
import { afterEach, describe, expect, it, vi } from "vitest";

import fixture from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { applyEvent, createInitialState, createStateStore } from "../state";
import { parseEvent } from "./events";
import { CommandError, createCommandClient, startLive, startReplay } from "./index";
import type { EventStream } from "./index";

const log = fixture.trim().split("\n").map(parseEvent);
const first = log[0];
const second = log[1];
if (first?.type !== "run.started" || second?.type !== "graph.snapshot") {
  throw new Error("Fixture must begin with run.started and graph.snapshot");
}
const run = first;
const graph = second;
const snapshot: GraphResponse = { run_id: run.payload.run_id, seq: 2, graph: graph.payload };

class FakeSource implements EventStream {
  onmessage: EventStream["onmessage"] = null;
  close = vi.fn();
  constructor(readonly url: string) {}
  emit(event: ServerEvent) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(event) }));
  }
}

function setup(responses: GraphResponse[] = [snapshot]) {
  const sources: FakeSource[] = [];
  const store = createStateStore();
  let calls = 0;
  const request = vi.fn<typeof fetch>(() => {
    const body = responses[Math.min(calls++, responses.length - 1)];
    return Promise.resolve(Response.json(body));
  });
  const transport = startLive({
    store,
    request,
    openStream: (url) => {
      const source = new FakeSource(url);
      sources.push(source);
      return source;
    },
  });
  const source = () => {
    const latest = sources.at(-1);
    if (!latest) throw new Error("Stream not connected");
    return latest;
  };
  return { store, request, transport, sources, source };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("live transport", () => {
  it("loads the snapshot then reconstructs history and applies events in order", async () => {
    const { transport, store, request, source } = setup();
    await transport.ready;
    expect(request).toHaveBeenCalledWith("/graph", expect.objectContaining({ method: "GET" }));
    expect(store.getState().state.graph).toEqual(snapshot.graph);
    // Proposal 4.6: the graph response cannot hydrate agents and changes, so replay from 1.
    expect(source().url).toBe(`/events?after=0&run=${run.payload.run_id}`);
    for (const event of log) source().emit(event);
    expect(store.getState().state).toEqual(log.reduce(applyEvent, createInitialState()));
    transport.stop();
    expect(source().close).toHaveBeenCalledOnce();
  });

  it("refetches on a gap and resumes from the last applied seq without losing agent events", async () => {
    const { transport, store, request, source, sources } = setup([
      snapshot,
      { ...snapshot, seq: 4 },
    ]);
    await transport.ready;
    source().emit(run);
    source().emit(graph);
    const old = source();
    old.emit({ ...graph, seq: 4 });
    await vi.waitFor(() => {
      expect(sources).toHaveLength(2);
    });
    expect(request).toHaveBeenCalledTimes(2);
    expect(old.close).toHaveBeenCalledOnce();
    expect(source().url).toContain("after=2&run=");
    const third = log[2];
    const fourth = log[3];
    if (!third || !fourth) throw new Error("Missing fixture events");
    source().emit(third);
    source().emit(fourth);
    expect(store.getState().state.lastSeq).toBe(4);
    expect(store.getState().state.agents).not.toEqual({});
    old.emit({ ...graph, seq: 99 });
    expect(request).toHaveBeenCalledTimes(2);
    transport.stop();
  });

  it("refetches on a graph from_version mismatch and heals that event with the checkpoint", async () => {
    const healed = { ...snapshot, seq: 3, graph: { ...snapshot.graph, version: "healed" } };
    const { transport, store, source, sources } = setup([snapshot, healed]);
    await transport.ready;
    source().emit(run);
    source().emit(graph);
    const delta: ServerEvent = {
      seq: 3,
      ts: graph.ts,
      type: "graph.delta",
      payload: {
        from_version: "wrong",
        to_version: "healed",
        commit: "commit",
        cause: "rescan",
        nodes_added: [],
        nodes_changed: [],
        nodes_removed: [],
        edges_added: [],
        edges_changed: [],
        edges_removed: [],
        parse_errors: [],
      },
    };
    source().emit(delta);
    await vi.waitFor(() => {
      expect(sources).toHaveLength(2);
    });
    expect(source().url).toContain("after=2&run=");
    source().emit(delta);
    expect(store.getState().state).toMatchObject({ lastSeq: 3, gap: false, graph: healed.graph });
    transport.stop();
  });

  it("leaves native reconnection alone and recovers if its next event is beyond a gap", async () => {
    const { transport, source, sources } = setup();
    await transport.ready;
    source().emit(run);
    source().emit(graph);
    // A browser reconnect reuses this instance and its Last-Event-ID. An in-order event
    // requires no replacement; a missed event on that connection does.
    source().emit({ ...graph, seq: 3 });
    expect(sources).toHaveLength(1);
    source().emit({ ...graph, seq: 5 });
    await vi.waitFor(() => {
      expect(sources).toHaveLength(2);
    });
    expect(source().url).toContain("after=3&run=");
    transport.stop();
  });

  it("resets all old state when a reconnect announces another run", async () => {
    const newRun = { ...run, payload: { ...run.payload, run_id: "run-20261028-000000" } };
    const { transport, store, source, sources } = setup([
      snapshot,
      { ...snapshot, run_id: newRun.payload.run_id },
    ]);
    await transport.ready;
    for (const event of log) source().emit(event);
    expect(store.getState().state.changes).not.toEqual({});
    source().emit(newRun);
    expect(store.getState().state).toEqual({
      ...createInitialState(),
      run: newRun.payload,
      lastSeq: 1,
    });
    await vi.waitFor(() => {
      expect(sources).toHaveLength(2);
    });
    expect(source().url).toBe(`/events?after=1&run=${newRun.payload.run_id}`);
    source().emit(graph);
    expect(store.getState().state.lastSeq).toBe(2);
    transport.stop();
  });

  it("does not connect or mutate after disposal while GET /graph is pending", async () => {
    let resolve: (response: Response) => void = () => {
      throw new Error("Not pending");
    };
    const request = vi.fn<typeof fetch>(
      () =>
        new Promise<Response>((done) => {
          resolve = done;
        }),
    );
    const store = createStateStore();
    const openStream = vi.fn<(url: string) => EventStream>();
    const transport = startLive({ store, request, openStream });
    transport.stop();
    resolve(Response.json(snapshot));
    await transport.ready;
    expect(openStream).not.toHaveBeenCalled();
    expect(store.getState().state).toEqual(createInitialState());
    expect(request.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
  });
});

describe("browser replay", () => {
  it("applies the bundled fixture through the same reducer in order without network", async () => {
    const store = createStateStore();
    const request = vi.fn<typeof fetch>();
    const seqs: number[] = [];
    store.subscribe(({ state }) => {
      seqs.push(state.lastSeq);
    });
    const transport = startReplay({ store, source: { text: fixture }, speed: 0, request });
    await transport.ready;
    expect(seqs).toEqual([0, ...log.map((event) => event.seq)]);
    expect(store.getState().state).toEqual(log.reduce(applyEvent, createInitialState()));
    expect(request).not.toHaveBeenCalled();
    transport.stop();
  });

  it("loads a URL and caps each gap after dividing by speed, like the backend", async () => {
    vi.useFakeTimers();
    const store = createStateStore();
    const request = vi.fn<typeof fetch>().mockResolvedValue(new Response(fixture));
    const transport = startReplay({
      store,
      source: { url: "/recordings/run.jsonl" },
      speed: 2,
      request,
    });
    await vi.advanceTimersByTimeAsync(0);
    let elapsed = 0;
    for (let i = 1; i < log.length; i++) {
      const current = log[i];
      const previous = log[i - 1];
      if (!current || !previous) throw new Error("Missing event");
      const delay = Math.min(
        Math.max(Date.parse(current.ts) - Date.parse(previous.ts), 0) / 2,
        2000,
      );
      await vi.advanceTimersByTimeAsync(delay);
      elapsed += delay;
      expect(store.getState().state.lastSeq).toBe(current.seq);
    }
    await transport.ready;
    expect(elapsed).toBeGreaterThan(2000);
    expect(request.mock.calls[0]?.[0]).toBe("/recordings/run.jsonl");
    expect(request.mock.calls[0]?.[1]?.signal).toBeInstanceOf(AbortSignal);
  });

  it("cancels a pending delay and settles without applying another event", async () => {
    vi.useFakeTimers();
    const store = createStateStore();
    const transport = startReplay({ store, source: { text: fixture } });
    expect(store.getState().state.lastSeq).toBe(1);
    transport.stop();
    await transport.ready;
    await vi.runAllTimersAsync();
    expect(store.getState().state.lastSeq).toBe(1);
  });

  it("rejects an out-of-order log rather than silently skipping events", async () => {
    const store = createStateStore();
    const badGraph: GraphSnapshotEvent = { ...graph, seq: 3 };
    const transport = startReplay({
      store,
      source: { text: [run, badGraph].map((event) => JSON.stringify(event)).join("\n") },
      speed: 0,
    });
    await expect(transport.ready).rejects.toThrow("mismatch at 3");
    expect(store.getState().state.lastSeq).toBe(1);
  });
});

describe("command client", () => {
  it("maps the single error body to a typed error including status and code", async () => {
    const request = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        Response.json(
          { error: { code: "wrong_state", message: "Change is not ready" } },
          { status: 409 },
        ),
      );
    const client = createCommandClient(request);
    const failure = await client
      .approveChange("change-1", { comment: "Looks good" })
      .catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(CommandError);
    expect(failure).toMatchObject({
      status: 409,
      code: "wrong_state",
      message: "Change is not ready",
    });
  });

  it("uses the contract paths, bodies and query encoding for every command", async () => {
    const request = vi
      .fn<typeof fetch>()
      .mockImplementation((_url, init) =>
        Promise.resolve(
          init?.method === "POST" && _url !== "/agents"
            ? new Response(null, { status: 202 })
            : Response.json({}),
        ),
      );
    const client = createCommandClient(request);
    await client.startAgent({ task: "Fix price", slot: "A", model: "fake" });
    await client.stopAgent("a/b");
    await client.messageAgent("a/b", { text: "Hello" });
    await client.pauseAgent("a/b");
    await client.resumeAgent("a/b");
    await client.approveChange("c/d", { comment: "Fine" });
    await client.requestChanges("c/d", { comment: "Try again" });
    await client.rejectChange("c/d");
    await client.getGraph();
    await client.rescanGraph();
    await client.getImpact("shop:price", 3, "c/d");
    await client.getFile("shop/a b.py", "a/b");
    await client.saveFile({ path: "shop/a.py", content: "x", base_hash: "hash", agent_id: "a/b" });
    await client.getHealth();
    expect(request.mock.calls.map(([url, init]) => [url, init?.method, init?.body])).toEqual([
      ["/agents", "POST", JSON.stringify({ task: "Fix price", slot: "A", model: "fake" })],
      ["/agents/a%2Fb/stop", "POST", undefined],
      ["/agents/a%2Fb/message", "POST", JSON.stringify({ text: "Hello" })],
      ["/agents/a%2Fb/pause", "POST", undefined],
      ["/agents/a%2Fb/resume", "POST", undefined],
      ["/changes/c%2Fd/approve", "POST", JSON.stringify({ comment: "Fine" })],
      ["/changes/c%2Fd/request-changes", "POST", JSON.stringify({ comment: "Try again" })],
      ["/changes/c%2Fd/reject", "POST", "{}"],
      ["/graph", "GET", undefined],
      ["/graph/rescan", "POST", undefined],
      ["/impact?node=shop%3Aprice&depth=3&change=c%2Fd", "GET", undefined],
      ["/files?path=shop%2Fa+b.py&agent=a%2Fb", "GET", undefined],
      [
        "/files",
        "PUT",
        JSON.stringify({ path: "shop/a.py", content: "x", base_hash: "hash", agent_id: "a/b" }),
      ],
      ["/health", "GET", undefined],
    ]);
    expect(request.mock.calls[0]?.[1]?.headers).toEqual({ "Content-Type": "application/json" });
    expect(request.mock.calls[1]?.[1]?.headers).toBeUndefined();
  });
});
