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

function backlogPreviews(mode: "agent" | "revision") {
  const { state: prefix, port, host, request: earlier } = setup();
  host.dispose();
  const agent = prefix.agents["agent-1"];
  const proposal = prefix.changes["change-agent-1"]?.proposal;
  if (!agent || !proposal) throw new Error("Missing proposal");
  prefix.lastSeq++; // The earlier preview status is the next applied backlog event.
  const agentId = mode === "agent" ? "agent-2" : agent.agent_id;
  const changeId = `change-${agentId}`;
  const request: LeadView = {
    ...earlier,
    through_seq: 11,
    view: { kind: "preview", agent_id: agentId },
  };
  const arrivals: ServerEvent[] = [
    {
      type: "agent.status",
      seq: 10,
      ts: "same",
      payload: { ...agent, agent_id: agentId, change_id: changeId },
    },
    {
      type: "change.proposed",
      seq: 11,
      ts: "same",
      payload: {
        ...proposal,
        agent_id: agentId,
        change_id: changeId,
        revision: 2,
        head_commit: "later-head",
      },
    },
    { type: "lead.view", seq: 12, ts: "same", payload: request },
  ];
  const complete = arrivals.reduce(applyEvent, prefix);
  const candidate = resolvePreviewReference(complete, request).candidate;
  const oldCandidate = resolvePreviewReference(prefix, earlier).candidate;
  if (!candidate || !oldCandidate) throw new Error("Missing preview candidates");
  return { prefix, complete, port, candidate, oldCandidate };
}

it.each(
  (["agent", "revision"] as const).flatMap((mode) =>
    (["arrival", "identity"] as const).map((trigger) => ({ mode, trigger })),
  ),
)(
  "retains ownership across an earlier $mode preview and recovers on $trigger",
  async ({ mode, trigger }) => {
    vi.useFakeTimers();
    const { prefix, complete, port, candidate, oldCandidate } = backlogPreviews(mode);
    const receipt = await port.start(
      {
        run_id: candidate.run_id,
        agent_id: candidate.agent_id,
        revision: candidate.revision,
        head_commit: candidate.head_commit,
      },
      new AbortController().signal,
    );
    const identity = { run: candidate.run_id, source: "live" };
    let saved: PreviewOwnership | null = { identity, candidate, ...receipt };
    const store: PreviewOwnershipStore = {
      load: () => saved,
      save: vi.fn(),
      clear: vi.fn(() => {
        saved = null;
      }),
    };
    let replay = prefix;
    const recovered = createPreviewHost(
      port,
      previewClock,
      identity,
      () => replay,
      () => true,
      undefined,
      store,
    );
    const earlierStatus: PreviewStatusEvent = {
      type: "preview.status",
      seq: prefix.lastSeq,
      ts: "same",
      payload: {
        ...oldCandidate,
        change_id: oldCandidate.change_id,
        preview_id: `preview-${"a".repeat(32)}`,
        state: "stopped",
        reason: "closed",
      },
    };
    recovered.accept(earlierStatus);
    await vi.advanceTimersByTimeAsync(0);
    expect(store.clear).not.toHaveBeenCalled();
    expect(store.load(identity)?.preview_id).toBe(receipt.preview_id);
    expect(port.calls.map((call) => call.kind)).toEqual(["start"]);

    replay = complete;
    const status = port.publicStatus();
    if (!status) throw new Error("Missing owned preview");
    if (trigger === "arrival")
      recovered.accept({ ...earlierStatus, seq: replay.lastSeq, payload: status });
    else await recovered.setIdentity(identity);
    await vi.advanceTimersByTimeAsync(0);
    expect(recovered.controller.snapshot()).toMatchObject({
      owned: true,
      visible: false,
      frameUrl: null,
      status,
    });
    expect(recovered.snapshot()).toBe(false);
    expect(port.calls.map((call) => call.kind)).toEqual(["start", "details", "renew"]);
    await recovered.close();
    expect(saved).toBeNull();
    recovered.dispose();
  },
);

it("retains a public bootstrap arrival for an empty-store observer without start or authority", async () => {
  const { host: owner, state, port, request } = setup();
  await owner.activate(request);
  const store: PreviewOwnershipStore = { load: () => null, save: vi.fn(), clear: vi.fn() };
  const observer = createPreviewHost(
    port,
    previewClock,
    { run: "", source: "live" },
    () => state,
    () => true,
    undefined,
    store,
  );
  const status = port.publicStatus();
  if (!status) throw new Error("Missing preview");
  observer.accept({ type: "preview.status", seq: state.lastSeq, ts: "same", payload: status });
  await observer.setIdentity({ run: status.run_id, source: "live" });
  const before = port.calls.length;
  await observer.activate(request);
  expect(observer.controller.snapshot()).toMatchObject({ owned: false, frameUrl: null, status });
  expect(port.calls).toHaveLength(before);
  await observer.close();
  observer.accept({ type: "preview.status", seq: state.lastSeq, ts: "same", payload: status });
  await observer.controller.see(() => resolvePreviewReference(state, request).candidate);
  expect(port.calls).toHaveLength(before);
  observer.dispose();
  expect(port.calls).toHaveLength(before);
  await owner.close();
  owner.dispose();
});

it("retains observation arriving while bootstrap identity is settling", async () => {
  const { host: owner, state, port, request } = setup();
  await owner.activate(request);
  const observer = createPreviewHost(
    port,
    previewClock,
    { run: "", source: "live" },
    () => state,
    () => true,
  );
  const changing = observer.setIdentity({ run: state.run?.run_id ?? "", source: "live" });
  const status = port.publicStatus();
  if (!status) throw new Error("Missing preview");
  observer.accept({ type: "preview.status", seq: state.lastSeq, ts: "same", payload: status });
  await changing;
  const count = port.calls.length;
  await observer.activate(request);
  expect(port.calls).toHaveLength(count);
  expect(observer.controller.snapshot().status).toEqual(status);
  observer.dispose();
  await owner.close();
});

it("bootstrap and Chat selection navigation leave a hidden recovered owner renewing until explicit activation or Close", async () => {
  const { host: owner, port, state, request } = setup();
  await owner.activate(request);
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
    save: (value) => {
      saved = value;
    },
    clear: () => {
      saved = null;
    },
  };
  const recovered = createPreviewHost(
    port,
    previewClock,
    { run: "", source: "live" },
    () => state,
    () => true,
    undefined,
    store,
  );
  await recovered.navigate();
  await recovered.setIdentity({ run: candidate.run_id, source: "live" });
  await recovered.navigate();
  await recovered.controller.navigate();
  expect(port.calls.filter((call) => call.kind === "stop")).toHaveLength(0);
  expect(recovered.controller.snapshot()).toMatchObject({
    owned: true,
    visible: false,
    frameUrl: null,
  });
  await recovered.activate(request);
  expect(recovered.controller.snapshot().frameUrl).toBe(port.frameUrl);
  await recovered.navigate();
  expect(port.calls.filter((call) => call.kind === "stop")).toHaveLength(1);
  expect(recovered.snapshot()).toBe(false);
  recovered.dispose();
});

it.each(["revision", "change", "head", "absent", "older"] as const)(
  "%s applied candidate clears superseded ownership or keeps waiting without slot intent or I/O",
  async (mode) => {
    const { prefix, complete, port, candidate, oldCandidate } = backlogPreviews("revision");
    const identity = { run: candidate.run_id, source: "live" };
    const stored = {
      ...oldCandidate,
      ...(mode === "change" && { change_id: "superseded-change" }),
      ...(mode === "head" && { head_commit: "superseded-head" }),
    };
    const record: PreviewOwnership = {
      identity,
      candidate: mode === "older" ? candidate : stored,
      preview_id: `preview-${"a".repeat(32)}`,
      lease_token: "private-token",
    };
    let saved: PreviewOwnership | null = record;
    const store: PreviewOwnershipStore = {
      load: () => saved,
      save: vi.fn(),
      clear: vi.fn(() => {
        saved = null;
      }),
    };
    const state = mode === "revision" ? complete : prefix;
    if (mode === "absent")
      state.leadViews = {
        lead: (state.leadViews.lead ?? []).map((event) => ({
          ...event,
          payload: { ...event.payload, view: { kind: "preview", agent_id: "absent-agent" } },
        })),
      };
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
    await recovered.setIdentity(identity);
    await recovered.setIdentity(identity);
    const waiting = mode === "absent" || mode === "older";
    expect(saved).toEqual(waiting ? record : null);
    expect(store.clear).toHaveBeenCalledTimes(waiting ? 0 : 1);
    if (!waiting) expect(store.clear).toHaveBeenCalledWith(record);
    expect(recovered.snapshot()).toBe(false);
    expect(recovered.controller.snapshot()).toMatchObject({ owned: false, visible: false });
    expect(publish).not.toHaveBeenCalled();
    recovered.dispose();
    expect(port.calls).toEqual([]);
    expect(store.save).not.toHaveBeenCalled();
  },
);
