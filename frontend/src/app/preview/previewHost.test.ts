import type { PreviewOwnership, PreviewOwnershipStore } from "./previewOwnership";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { LeadView, PreviewStatusEvent, ServerEvent } from "@contracts";
import fixture from "../../../../fixtures/views/reference-cases.json";
import { resolvePreviewReference, applyEvent, createInitialState } from "../../state";
import { createPreviewHost, previewClock } from "./previewHost";
import { PreviewFake, deferred } from "./previewFake";
import { previewStartup } from "./previewStartup";

vi.mock("./previewStartup", () => ({ previewStartup: vi.fn() }));
beforeEach(() => {
  vi.mocked(previewStartup).mockReturnValue({ kind: "missing" });
});

function setup() {
  const state = (fixture.events as ServerEvent[])
    .slice(0, 6)
    .reduce(applyEvent, createInitialState());
  const agent = state.agents["agent-1"];
  const change = state.changes["change-1"];
  if (!agent || !change) throw new Error("Missing fixture");
  state.agents[agent.agent_id] = { ...agent, state: "ready_for_review" };
  // Fake's change identity follows this documented test-only naming convention.
  state.changes = {
    ["change-agent-1"]: {
      ...change,
      change_id: "change-agent-1",
      proposal: change.proposal && { ...change.proposal, change_id: "change-agent-1" },
    },
  };
  state.agents[agent.agent_id] = {
    ...agent,
    state: "ready_for_review",
    change_id: "change-agent-1",
  };
  state.decisionEvents.push({
    type: "agent.status",
    seq: 7,
    ts: "2026-11-03T00:00:00Z",
    payload: {
      ...agent,
      state: "ready_for_review",
      change_id: state.agents[agent.agent_id]?.change_id ?? agent.change_id,
    },
  });
  state.decisionEvents = state.decisionEvents.map((event) =>
    event.type === "change.proposed"
      ? { ...event, payload: { ...event.payload, change_id: "change-agent-1" } }
      : event,
  );
  const request: LeadView = {
    agent_id: "lead",
    through_seq: 7,
    caption: null,
    view: { kind: "preview", agent_id: agent.agent_id },
  };
  state.leadViews.lead = [{ type: "lead.view", seq: 8, ts: "same", payload: request }];
  state.lastSeq = 8;
  const port = new PreviewFake(previewClock);
  let allowed = true;
  const host = createPreviewHost(
    port,
    previewClock,
    { run: state.run?.run_id ?? "", source: "live" },
    () => state,
    () => allowed,
  );
  return {
    state,
    port,
    host,
    request,
    disallow: () => {
      allowed = false;
    },
  };
}
afterEach(() => {
  vi.useRealTimers();
});
it("requires person intent; ready events GET a frame, renewal continues, close restores the slot", async () => {
  vi.useFakeTimers();
  const { state, port, host, request } = setup();
  port.launchState = "starting";
  await host.activate(request);
  expect(host.snapshot()).toBe(true);
  expect(host.controller.snapshot().frameUrl).toBeNull();
  const ready = port.publish("ready", null);
  if (!ready) throw new Error("Missing status");
  state.lastSeq++;
  host.accept({ type: "preview.status", seq: state.lastSeq, ts: "same", payload: ready });
  await vi.advanceTimersByTimeAsync(0);
  expect(host.controller.snapshot().frameUrl).toBe(port.frameUrl);
  await vi.advanceTimersByTimeAsync(10_000);
  expect(port.calls.filter((c) => c.kind === "renew")).toHaveLength(1);
  await host.close();
  expect(host.snapshot()).toBe(false);
  host.accept({ type: "preview.status", seq: state.lastSeq, ts: "same", payload: ready });
  expect(host.snapshot()).toBe(false);
  host.dispose();
});
it("observation cannot open a slot, and replay/cross-run/gap callbacks cannot start", () => {
  const { host, port, request, state, disallow } = setup();
  const event: PreviewStatusEvent = {
    type: "preview.status",
    seq: state.lastSeq,
    ts: "same",
    payload: {
      preview_id: `preview-${"a".repeat(32)}`,
      run_id: state.run?.run_id ?? "",
      agent_id: "agent-1",
      change_id: "change-agent-1",
      revision: 1,
      head_commit: "head",
      state: "ready",
      reason: null,
    },
  };
  host.accept(event);
  expect(host.snapshot()).toBe(false);
  disallow();
  void host.activate(request);
  expect(port.calls).toHaveLength(0);
  host.dispose();
});
it("unverified Stop masks the frame, retains closure feedback and blocks replacement", async () => {
  const { host, port, request } = setup();
  await host.activate(request);
  port.stopMode = "unverified";
  await host.close();
  expect(host.snapshot()).toBe(true);
  expect(host.controller.snapshot().frameUrl).toBeNull();
  await host.activate(request);
  expect(port.calls.filter((c) => c.kind === "start")).toHaveLength(1);
  host.dispose();
});
it("duplicate activation and navigation during start drain the late receipt without reopening", async () => {
  const { host, port, request } = setup();
  const gate = deferred<undefined>();
  const start = port.start.bind(port);
  vi.spyOn(port, "start").mockImplementation(async (input, signal) => {
    await gate.promise;
    return start(input, signal);
  });
  const opening = host.activate(request);
  void host.activate(request);
  const closing = host.navigate();
  gate.resolve(undefined);
  await opening;
  await closing;
  expect(port.calls.filter((c) => c.kind === "start")).toHaveLength(1);
  expect(host.snapshot()).toBe(false);
  expect(host.controller.snapshot().frameUrl).toBeNull();
  host.dispose();
});

it("returns cached slot and panel snapshots between changes, including a masked frame", async () => {
  const { host, request, state } = setup();
  const listener = vi.fn();
  const unsubscribe = host.controller.subscribe(listener);
  const initial = host.controller.snapshot();
  expect(host.controller.snapshot()).toBe(initial);
  expect(host.snapshot()).toBe(false);
  await host.setIdentity({ run: state.run?.run_id ?? "", source: "live" });
  expect(host.controller.snapshot()).toBe(initial);
  expect(listener).not.toHaveBeenCalled();

  await host.activate(request);
  const ready = host.controller.snapshot();
  expect(ready.frameUrl).not.toBeNull();
  expect(host.controller.snapshot()).toBe(ready);
  expect(host.snapshot()).toBe(true);

  const closing = host.navigate();
  const masked = host.controller.snapshot();
  expect(masked.frameUrl).toBeNull();
  expect(host.controller.snapshot()).toBe(masked);
  await closing;
  const closed = host.controller.snapshot();
  expect(host.controller.snapshot()).toBe(closed);
  expect(host.snapshot()).toBe(false);
  unsubscribe();
  host.dispose();
});

it.each(["valid", "missing", "invalid"] as const)(
  "enforces %s startup even for direct host activation before publishing a slot",
  async (kind) => {
    // Reader output is mocked; validation and byte limits belong to the frozen reader tests.
    vi.mocked(previewStartup).mockReturnValue(
      kind === "valid" ? { kind, report: {} as never } : { kind },
    );
    const { host, port, request } = setup();
    const publish = vi.fn();
    host.subscribe(publish);
    await host.activate(request);
    expect(port.calls.filter((call) => call.kind === "start")).toHaveLength(
      kind === "invalid" ? 0 : 1,
    );
    expect(host.snapshot()).toBe(kind !== "invalid");
    expect(host.controller.snapshot().frameUrl !== null).toBe(kind !== "invalid");
    if (kind === "invalid") {
      expect(port.calls).toEqual([]);
      expect(publish).not.toHaveBeenCalled();
      expect(host.controller.snapshot()).toMatchObject({ visible: false, pending: false });
    }
    host.dispose();
  },
);

it("rechecks startup after authenticated drainage before resolving the deferred candidate", async () => {
  const { host, port, request } = setup();
  await host.activate(request);
  const drainage = deferred<undefined>();
  const stop = port.stop.bind(port);
  vi.spyOn(port, "stop").mockImplementation(async (...args) => {
    await drainage.promise;
    return stop(...args);
  });
  const replacing = host.activate(request);
  vi.mocked(previewStartup).mockReturnValue({ kind: "invalid" });
  drainage.resolve(undefined);
  await replacing;
  expect(port.calls.filter((call) => call.kind === "start")).toHaveLength(1);
  expect(host.controller.snapshot()).toMatchObject({ pending: false, frameUrl: null });
  host.dispose();
});

it("recovers after bootstrap without granting slot intent and activation reuses that preview", async () => {
  const { host, port, state, request } = setup();
  await host.activate(request);
  const candidate = resolvePreviewReference(state, request).candidate;
  if (!candidate) throw new Error("Missing candidate");
  let saved: PreviewOwnership | null = {
    identity: { run: candidate.run_id, source: "live" },
    candidate,
    preview_id: port.publicStatus()?.preview_id ?? "",
    lease_token: "fake-private-1",
  };
  const store: PreviewOwnershipStore = {
    load: () => saved,
    save: (record) => {
      saved = record;
    },
    clear: () => {
      saved = null;
    },
  };
  const identity = { run: "", source: "live" };
  const recovered = createPreviewHost(
    port,
    previewClock,
    identity,
    () => state,
    () => true,
    undefined,
    store,
  );
  const publish = vi.fn();
  recovered.subscribe(publish);
  await recovered.setIdentity({ ...identity, run: candidate.run_id });
  expect(recovered.controller.snapshot()).toMatchObject({
    owned: true,
    visible: false,
    frameUrl: null,
  });
  expect(recovered.snapshot()).toBe(false);
  expect(publish).not.toHaveBeenCalled();
  await recovered.activate(request);
  expect(recovered.controller.snapshot().frameUrl).toBe(port.frameUrl);
  expect(port.calls.filter((call) => call.kind === "start")).toHaveLength(1);
  await recovered.close();
  expect(saved).toBeNull();
  recovered.dispose();
});
it.each(["history", "invalid", "gap"])(
  "does not recover through a %s permission fence",
  async (mode) => {
    const { port, state } = setup();
    const store: PreviewOwnershipStore = { load: vi.fn(() => null), save: vi.fn(), clear: vi.fn() };
    if (mode === "invalid") vi.mocked(previewStartup).mockReturnValue({ kind: "invalid" });
    if (mode === "gap") state.gap = { expected: 1, received: 3 } as never;
    const host = createPreviewHost(
      port,
      previewClock,
      { run: state.run?.run_id ?? "", source: "live" },
      () => state,
      () => mode !== "history",
      undefined,
      store,
    );
    await host.setIdentity({ run: state.run?.run_id ?? "", source: "live" });
    expect(port.calls).toEqual([]);
    expect(host.snapshot()).toBe(false);
    host.dispose();
  },
);

it("does not consume a persisted owner before bootstrap supplies preview references", async () => {
  const { state, port } = setup();
  const views = state.leadViews;
  state.leadViews = {};
  const store: PreviewOwnershipStore = { load: vi.fn(() => null), save: vi.fn(), clear: vi.fn() };
  const host = createPreviewHost(
    port,
    previewClock,
    { run: "", source: "live" },
    () => state,
    () => true,
    undefined,
    store,
  );
  await host.setIdentity({ run: state.run?.run_id ?? "", source: "live" });
  expect(store.load).not.toHaveBeenCalled();
  state.leadViews = views;
  await host.setIdentity({ run: state.run?.run_id ?? "", source: "live" });
  expect(store.load).toHaveBeenCalledTimes(1);
  host.dispose();
});
