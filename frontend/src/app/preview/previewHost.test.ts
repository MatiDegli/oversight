import { afterEach, expect, it, vi } from "vitest";
import type { LeadView, PreviewStatusEvent, ServerEvent } from "@contracts";
import fixture from "../../../../fixtures/views/reference-cases.json";
import { applyEvent, createInitialState } from "../../state";
import { createPreviewHost, previewClock } from "./previewHost";
import { PreviewFake, deferred } from "./previewFake";
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
  state.agents[agent.agent_id] = { ...agent, state: "ready_for_review", change_id: "change-agent-1" };
  const request: LeadView = {
    agent_id: "lead",
    through_seq: 6,
    caption: null,
    view: { kind: "preview", agent_id: agent.agent_id },
  };
  state.leadViews.lead = [{ type: "lead.view", seq: 7, ts: "same", payload: request }];
  state.lastSeq = 7;
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
