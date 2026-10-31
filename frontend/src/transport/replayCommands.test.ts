import type { GraphSnapshotEvent } from "@contracts";
import { describe, expect, it, vi } from "vitest";
import fixture from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { applyEvent, createInitialState, createStateStore } from "../state";
import { CommandError, createCommandClient, startReplay } from "./index";
import { log, run, graph } from "./transport.support";

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
  it("keeps too_large as a typed HTTP 413 error", async () => {
    const request = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        Response.json(
          { error: { code: "too_large", message: "File exceeds the server size limit" } },
          { status: 413 },
        ),
      );
    await expect(createCommandClient(request).getGraph()).rejects.toMatchObject({
      status: 413,
      code: "too_large",
      message: "File exceeds the server size limit",
    });
  });

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
