import type { ServerEvent } from "@contracts";
import { describe, expect, it, vi } from "vitest";
import reservedA from "../../../fixtures/contracts/reserved-1.4.json";
import reserved from "../../../fixtures/contracts/reserved-1.3.json";
import { applyEvent, createInitialState, createStateStore } from "../state";
import { parseEvent } from "./events";
import { startLive } from "./index";
import type { EventStream } from "./index";
import { log, run, graph, FakeSource, stops, setup } from "./transport.support";

describe("live transport", () => {
  it("reports a connected stream even with no new events and ignores opens after stop", async () => {
    const onOpen = vi.fn();
    const { source, transport, onError } = setup(onOpen);
    await transport.ready;
    source().open();
    expect(onOpen).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
    transport.stop();
    source().open();
    expect(onOpen).toHaveBeenCalledOnce();
  });
  it("retains reserved inbox evidence in live streams without enabling commands", async () => {
    const { transport, store, source, onError } = setup();
    await transport.ready;
    for (const event of log) source().emit(event);
    const before = store.getState().state;
    // Reserved inbox evidence keeps its envelope without enabling commands.
    // The next test covers the full mixed stream.
    const unwired = [
      ...reserved,
      ...reservedA.filter(
        (e) =>
          ![
            "change.resolved",
            "tests.executed",
            "change.facts_recorded",
            "change.reviewed",
          ].includes(e.type),
      ),
    ];
    expect(unwired.map((event) => event.type)).toEqual([
      "change.assessed",
      "merge_mode.changed",
      "plan.updated",
      "revert.resolved",
      "queue.updated",
      "lead.status",
    ]);
    for (const [index, example] of unwired.entries()) {
      source().raw(JSON.stringify({ ...example, seq: before.lastSeq + index + 1 }));
    }
    expect(onError).not.toHaveBeenCalled();
    expect(store.getState().state).toEqual({
      ...before,
      lastSeq: before.lastSeq + unwired.length,
      clock: reservedA.at(-1)?.ts,
      decisionEvents: [
        ...before.decisionEvents,
        ...unwired.flatMap((example, index) =>
          ["change.assessed", "plan.updated", "revert.resolved", "lead.status"].includes(
            example.type,
          )
            ? [{ ...example, seq: before.lastSeq + index + 1 }]
            : [],
        ),
      ],
    });
  });

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
    const review = delivered.find((event) => event.type === "change.reviewed");
    const prior = before.changes["change-1"];
    if (!review || !prior) throw new Error("Missing review example or change");
    if (!suite || !facts) throw new Error("Missing suite and facts examples");
    expect(onError).not.toHaveBeenCalled();
    expect(store.getState().state).toEqual({
      ...before,
      lastSeq: before.lastSeq + reserved.length + reservedA.length - 1,
      clock: reservedA.at(-1)?.ts,
      changes: {
        ...before.changes,
        "change-1": {
          ...prior,
          reviews: [{ seq: review.seq, review: review.payload }],
        },
      },
      testExecutions: { [suite.seq]: suite.payload },
      changeFacts: { [facts.payload.change_id]: [facts.payload] },
      decisionEvents: [
        ...before.decisionEvents,
        ...delivered.filter((event) =>
          [
            "change.assessed",
            "plan.updated",
            "revert.resolved",
            "lead.status",
            "change.facts_recorded",
          ].includes(event.type),
        ),
      ],
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
      runStartedAt: newRun.ts,
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
