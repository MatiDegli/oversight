import type { GraphSnapshotEvent, ServerEvent } from "@contracts";
import { afterEach, describe, expect, it, vi } from "vitest";

import reservedA from "../../../fixtures/contracts/reserved-1.4.json";
import reserved from "../../../fixtures/contracts/reserved-1.3.json";
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
function setup() {
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

describe("live transport", () => {
  it("keeps wired review evidence while consuming the remaining reserved events", async () => {
    const { transport, store, source, onError } = setup();
    await transport.ready;
    for (const event of log) source().emit(event);
    const before = store.getState().state;
    const delivered = [...reserved, ...reservedA.filter((e) => e.type !== "change.resolved")].map(
      (example, index) =>
        parseEvent(JSON.stringify({ ...example, seq: before.lastSeq + index + 1 })),
    );
    for (const event of delivered) source().emit(event);
    const suite = delivered.find((event) => event.type === "tests.executed");
    const facts = delivered.find((event) => event.type === "change.facts_recorded");
    if (!suite || !facts) throw new Error("Missing suite and facts examples");
    expect(onError).not.toHaveBeenCalled();
    expect(store.getState().state).toEqual({
      ...before,
      lastSeq: before.lastSeq + reserved.length + reservedA.length - 1,
      clock: reservedA.at(-1)?.ts,
      testExecutions: { [suite.seq]: suite.payload },
      changeFacts: { [facts.payload.change_id]: [facts.payload] },
    });
  });

  it("connects directly without GET /graph and matches replay at every event", async () => {
    const { transport, store, request, source } = setup();
    await transport.ready;
    expect(request).not.toHaveBeenCalled();
    expect(source().url).toBe("/events?after=0");
    expect(store.getState().state).toEqual(createInitialState());
    let expected = createInitialState();
    for (const event of log) {
      source().emit(event);
      expected = applyEvent(expected, event);
      expect(store.getState().state).toEqual(expected);
    }
    expect(request).not.toHaveBeenCalled();
  });

  it("has no request option: the stream is its only way to the server", () => {
    const transport = startLive({
      store: createStateStore(),
      // @ts-expect-error `request` was dead: connecting never fetched anything.
      request: vi.fn(),
      openStream: (url) => new FakeSource(url),
    });
    stops.push(transport.stop);
  });

  it("clears the failure when the reopened stream is healthy, with nothing new to apply", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { source, sources, transport, onError } = setup();
    await transport.ready;
    source().emit(run);
    source().emit(graph);
    source().fail();
    expect(onError).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(250);
    expect(sources).toHaveLength(2);
    // Opening is not enough to clear anything the first time it is seen on the old stream.
    sources[0]?.open();
    expect(onError).toHaveBeenCalledOnce();
    source().open();
    expect(onError).toHaveBeenCalledTimes(2);
    expect(onError).toHaveBeenLastCalledWith(null);
    // Once per streak, and a later failure is a new streak that reports again.
    source().open();
    expect(onError).toHaveBeenCalledTimes(2);
    source().fail();
    expect(onError).toHaveBeenCalledTimes(3);
    expect(onError).toHaveBeenLastCalledWith(expect.any(Error));
  });

  it("keeps a bad event's failure visible across reopens until an event applies", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { source, sources, store, transport, onError } = setup();
    await transport.ready;
    source().emit(run);
    source().raw("{");
    expect(onError).toHaveBeenCalledOnce();
    for (let reopen = 1; reopen <= 3; reopen++) {
      await vi.advanceTimersByTimeAsync(30_000);
      expect(sources).toHaveLength(reopen + 1);
      // The reopened stream is healthy, then replays the event that broke the last one.
      source().open();
      source().raw("{");
    }
    expect(onError).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(30_000);
    source().open();
    source().emit(graph);
    expect(store.getState().state.lastSeq).toBe(2);
    expect(onError).toHaveBeenCalledOnce();
  });

  it("keeps a bad-event alert through a failed reconnect and healthy reopen until an event applies", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { source, sources, store, onError } = setup();
    source().emit(run);
    source().raw("{");
    expect(onError).toHaveBeenCalledOnce();
    const badEvent: unknown = onError.mock.calls[0]?.[0];
    expect(badEvent).toBeInstanceOf(Error);
    await vi.advanceTimersByTimeAsync(250);
    source().fail();
    await vi.advanceTimersByTimeAsync(500);
    expect(sources).toHaveLength(3);
    source().open();
    expect(store.getState().state.lastSeq).toBe(1);
    expect(onError).toHaveBeenCalledExactlyOnceWith(badEvent);
    source().emit(graph);
    expect(store.getState().state.lastSeq).toBe(2);
    // Applied events clear the shell's alert through its store subscription. The next connection
    // failure starts a fresh streak, whose alert can now be cleared by a healthy reopen alone.
    source().fail();
    expect(onError).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(250);
    source().open();
    expect(onError).toHaveBeenCalledTimes(3);
    expect(onError).toHaveBeenLastCalledWith(null);
  });

  it.each(["malformed JSON", "reducer exception", "sequence gap", "graph version"])(
    "backs off with jitter on repeated %s, reports once and resets on an applied event",
    async (failure) => {
      vi.useFakeTimers();
      vi.spyOn(Math, "random").mockReturnValue(0.5);
      const { store, request, transport, source, sources, onError } = setup();
      await transport.ready;
      source().emit(run);
      source().emit(graph);
      const delta: ServerEvent = {
        seq: 3,
        ts: graph.ts,
        type: "graph.delta",
        payload: {
          from_version: "wrong",
          to_version: "next",
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
      const fail = () => {
        if (failure === "malformed JSON") source().raw("{");
        else if (failure === "reducer exception")
          source().raw(
            JSON.stringify({ ...delta, payload: { from_version: graph.payload.version } }),
          );
        else if (failure === "sequence gap")
          source().emit({ ...graph, seq: store.getState().state.lastSeq + 2 });
        else source().emit(delta);
      };
      // The window doubles until capped; equal jitter at random=.5 uses 75% of it.
      const delays = [375, 750, 1500, 3000, 6000, 12000, 22500, 22500];
      for (const [index, delay] of delays.entries()) {
        const old = source();
        fail();
        expect(old.close).toHaveBeenCalledOnce();
        expect(onError).toHaveBeenCalledTimes(1);
        expect(store.getState().state.lastSeq).toBe(2);
        await vi.advanceTimersByTimeAsync(delay - 1);
        expect(sources).toHaveLength(index + 1);
        await vi.advanceTimersByTimeAsync(1);
        expect(sources).toHaveLength(index + 2);
        expect(source().url).toBe(`/events?after=2&run=${run.payload.run_id}`);
        // Late messages/errors from a closed source cannot retry or change state.
        old.emit({ ...graph, seq: 99 });
        old.fail();
      }
      source().emit({ ...graph, seq: 3 });
      expect(store.getState().state.lastSeq).toBe(3);
      expect(store.getState().state.gap).toBe(false);
      fail();
      expect(onError).toHaveBeenCalledTimes(2);
      const count = sources.length;
      await vi.advanceTimersByTimeAsync(374);
      expect(sources).toHaveLength(count);
      await vi.advanceTimersByTimeAsync(1);
      expect(sources).toHaveLength(count + 1);
      expect(request).not.toHaveBeenCalled();
    },
  );

  it.each([0, 2])(
    "reopens EventSource after an error in readyState %s using the applied cursor",
    async (state) => {
      vi.useFakeTimers();
      vi.spyOn(Math, "random").mockReturnValue(0);
      const { source, sources, store, transport, onError } = setup();
      await transport.ready;
      source().emit(run);
      source().emit(graph);
      source().fail(state);
      await vi.advanceTimersByTimeAsync(249);
      expect(sources).toHaveLength(1);
      await vi.advanceTimersByTimeAsync(1);
      expect(sources).toHaveLength(2);
      expect(source().url).toBe(`/events?after=2&run=${run.payload.run_id}`);
      for (const event of log.slice(2)) source().emit(event);
      expect(store.getState().state).toEqual(log.reduce(applyEvent, createInitialState()));
      expect(onError).toHaveBeenCalledOnce();
    },
  );

  it("backs off if opening EventSource throws and cancels the pending retry on stop", async () => {
    vi.useFakeTimers();
    const openStream = vi.fn<(url: string) => EventStream>(() => {
      throw new Error("Unavailable");
    });
    const onError = vi.fn();
    const transport = startLive({ store: createStateStore(), openStream, onError });
    await transport.ready;
    expect(onError).toHaveBeenCalledOnce();
    transport.stop();
    await vi.runAllTimersAsync();
    expect(openStream).toHaveBeenCalledOnce();
  });

  it("cancels retries on disposal and ignores late callbacks", async () => {
    vi.useFakeTimers();
    const { transport, source, sources, store } = setup();
    const old = source();
    old.fail();
    transport.stop();
    old.emit(run);
    old.fail();
    await vi.runAllTimersAsync();
    expect(sources).toHaveLength(1);
    expect(store.getState().state).toEqual(createInitialState());
  });

  it("resets all old state through run.started without fetching or replacing the stream", () => {
    const { store, source, sources, request } = setup();
    for (const event of log) source().emit(event);
    expect(store.getState().state.changes).not.toEqual({});
    const newRun = { ...run, payload: { ...run.payload, run_id: "run-20261028-000000" } };
    source().emit(newRun);
    expect(store.getState().state).toEqual({
      ...createInitialState(),
      run: newRun.payload,
      lastSeq: 1,
      // The new run's first event is its "now"; nothing of the old run's times survives.
      clock: newRun.ts,
    });
    source().emit(graph);
    expect(store.getState().state.lastSeq).toBe(2);
    expect(sources).toHaveLength(1);
    expect(request).not.toHaveBeenCalled();
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
  it.each([
    [502, "<html>Bad gateway</html>"],
    [500, "Internal server error"],
    [500, ""],
    [502, "null"],
    [500, "{}"],
    [500, '{"error":null}'],
    [500, '{"error":{"code":"wrong_state","message":42}}'],
  ])("keeps HTTP %s with a non-contract body as CommandError (%s)", async (status, body) => {
    const request = vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status }));
    const failure: unknown = await createCommandClient(request)
      .getGraph()
      .catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(CommandError);
    expect(failure).toMatchObject({ status, code: undefined, message: `HTTP ${String(status)}` });
  });

  it.each([
    ['{"error":{"code":"invented","message":"No room"}}', "No room"],
    ['{"error":{"message":"No room"}}', "No room"],
    ['{"error":{"code":42,"message":"No room"}}', "No room"],
  ])("keeps the server's message when the code is not the contract's (%s)", async (body, text) => {
    const request = vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status: 500 }));
    const failure: unknown = await createCommandClient(request)
      .getGraph()
      .catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(CommandError);
    expect(failure).toMatchObject({ status: 500, code: undefined, message: text });
  });

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
      .approveChange("change-1", { revision: 1, head_commit: "head", comment: "Looks good" })
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
    await client.approveChange("c/d", { revision: 1, head_commit: "head", comment: "Fine" });
    await client.requestChanges("c/d", { revision: 1, head_commit: "head", comment: "Try again" });
    await client.rejectChange("c/d", { revision: 0, head_commit: "base" });
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
      [
        "/changes/c%2Fd/approve",
        "POST",
        JSON.stringify({ revision: 1, head_commit: "head", comment: "Fine" }),
      ],
      [
        "/changes/c%2Fd/request-changes",
        "POST",
        JSON.stringify({ revision: 1, head_commit: "head", comment: "Try again" }),
      ],
      ["/changes/c%2Fd/reject", "POST", JSON.stringify({ revision: 0, head_commit: "base" })],
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
