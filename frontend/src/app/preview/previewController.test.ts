import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createPreviewController } from "./previewController";
import { deferred, fakeCandidate, fakeStatus, PreviewFake } from "./previewFake";
import type { PreviewClock, PreviewPort } from "./previewPort";
import type { PreviewDetails, PreviewStatus } from "./previewTypes";

const clock: PreviewClock = {
  schedule(callback, delay) {
    const timer = setTimeout(callback, delay);
    return () => {
      clearTimeout(timer);
    };
  },
};
const identity = { run: fakeCandidate.run_id, source: "live-a" };
const next = { ...fakeCandidate, agent_id: "agent-b", revision: 2, head_commit: "new-head" };
function setup(port = new PreviewFake(clock)) {
  return { port, controller: createPreviewController(port, clock, identity) };
}
beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

it("owns only its own start, renews serially and exposes no token or persistence", async () => {
  const { port, controller } = setup();
  await controller.see(() => fakeCandidate);
  expect(controller.snapshot()).toMatchObject({ owned: true, frameUrl: port.frameUrl });
  await vi.advanceTimersByTimeAsync(20_000);
  expect(port.calls.filter((call) => call.kind === "renew")).toHaveLength(2);
  expect(JSON.stringify(controller.snapshot()) + JSON.stringify(port.calls)).not.toMatch(
    /lease_token|fake-private/,
  );
  expect(Object.keys(controller)).not.toContain("token");
  controller.dispose();
  await vi.advanceTimersByTimeAsync(60_000);
  expect(port.calls.filter((call) => call.kind === "renew")).toHaveLength(2);
});
it("reload and another tab observe without takeover, frame, renewal or authenticated Close", async () => {
  const { port, controller } = setup();
  await controller.see(() => fakeCandidate);
  const observer = createPreviewController(port, clock, identity);
  observer.observe(port.publicStatus() as PreviewStatus);
  await observer.see(() => next);
  await observer.refresh();
  await vi.advanceTimersByTimeAsync(10_000);
  expect(observer.snapshot()).toMatchObject({ owned: false, frameUrl: null });
  const before = port.calls.length;
  await observer.close();
  expect(observer.snapshot().visible).toBe(false);
  expect(port.calls).toHaveLength(before);
  controller.dispose();
});
it("renewal rejection cancels renewal, preserves frame and permits verified Close", async () => {
  const { port, controller } = setup();
  await controller.see(() => fakeCandidate);
  port.renewalRejects = true;
  await vi.advanceTimersByTimeAsync(10_000);
  expect(controller.snapshot()).toMatchObject({
    uncertain: true,
    notice: "renewal_failed",
    frameUrl: port.frameUrl,
  });
  await controller.see(() => next);
  await vi.advanceTimersByTimeAsync(10_000);
  expect(port.calls.filter((call) => call.kind === "renew")).toHaveLength(1);
  await controller.close();
  expect(controller.snapshot().visible).toBe(false);
});
it("keeps an observer's local dismissal through status updates and replacement until See preview", async () => {
  const { port, controller } = setup();
  await controller.see(() => fakeCandidate);
  const observer = createPreviewController(port, clock, identity);
  observer.observe(port.publicStatus() as PreviewStatus);
  await observer.close();
  port.publish("stopping", "closed");
  observer.observe(port.publicStatus() as PreviewStatus);
  expect(observer.snapshot()).toMatchObject({ visible: false, status: { state: "stopping" } });
  await controller.see(() => next);
  observer.observe(port.publicStatus() as PreviewStatus);
  expect(observer.snapshot()).toMatchObject({
    visible: false,
    owned: false,
    frameUrl: null,
    status: { agent_id: "agent-b" },
  });
  const before = port.calls.length;
  await observer.see(() => fakeCandidate);
  expect(observer.snapshot().visible).toBe(true);
  expect(port.calls).toHaveLength(before);
  controller.dispose();
});
it("quarantines failed details before a frame is available and allows verified Close", async () => {
  const { port, controller } = setup();
  vi.spyOn(port, "details").mockRejectedValue(new Error("Private network error"));
  await controller.see(() => fakeCandidate);
  expect(controller.snapshot()).toMatchObject({
    visible: true,
    owned: true,
    uncertain: true,
    notice: "closure_unconfirmed",
    frameUrl: null,
  });
  await controller.see(() => next);
  expect(port.calls.filter((call) => call.kind === "start")).toHaveLength(1);
  await controller.close();
  expect(controller.snapshot()).toMatchObject({ visible: false, owned: false });
});

it("awaits matching verified stop, then reads the fresh candidate once; repeated clicks do not race", async () => {
  const { port, controller } = setup();
  await controller.see(() => fakeCandidate);
  port.gate = deferred<undefined>();
  const read = vi.fn(() => next);
  const replacement = controller.see(read);
  await controller.see(read);
  expect(read).not.toHaveBeenCalled();
  expect(port.calls.map((call) => call.kind)).toEqual(["start", "details", "stop"]);
  expect(controller.snapshot().frameUrl).toBe(port.frameUrl);
  port.gate.resolve(undefined);
  await replacement;
  expect(read).toHaveBeenCalledTimes(1);
  expect(port.calls.map((call) => call.kind)).toEqual([
    "start",
    "details",
    "stop",
    "start",
    "details",
  ]);
  expect(port.calls[3]?.candidate).toEqual(next);
});
it.each(["reject", "unverified"] as const)(
  "quarantines %s stop and retains the same frame",
  async (mode) => {
    const { port, controller } = setup();
    await controller.see(() => fakeCandidate);
    port.stopMode = mode;
    await controller.see(() => next);
    await controller.see(() => next);
    await controller.close();
    expect(controller.snapshot()).toMatchObject({
      visible: true,
      uncertain: true,
      frameUrl: port.frameUrl,
    });
    expect(port.calls.filter((call) => call.kind === "start")).toHaveLength(1);
    expect(JSON.stringify(controller.snapshot())).not.toContain("Private child output");
    port.stopMode = "verified";
    await controller.close();
    expect(controller.snapshot()).toMatchObject({ visible: false, frameUrl: null });
  },
);
it("does not accept a terminal enum, wrong target or cleanup_unverified as drainage", async () => {
  for (const status of [
    fakeStatus("failed", "cleanup_unverified"),
    { ...fakeStatus("stopped", "closed"), agent_id: "other" },
    fakeStatus("stopping", "closed"),
  ]) {
    const fake = new PreviewFake(clock);
    const port: PreviewPort = {
      start: fake.start.bind(fake),
      details: fake.details.bind(fake),
      renew: fake.renew.bind(fake),
      stop: () => Promise.resolve(status),
    };
    const controller = createPreviewController(port, clock, identity);
    await controller.see(() => fakeCandidate);
    await controller.see(() => next);
    expect(controller.snapshot()).toMatchObject({ uncertain: true, frameUrl: fake.frameUrl });
    expect(fake.calls.filter((call) => call.kind === "start")).toHaveLength(1);
  }
});
it("keeps Close visible while pending and navigation sends only one stop", async () => {
  const { port, controller } = setup();
  await controller.see(() => fakeCandidate);
  port.gate = deferred<undefined>();
  const close = controller.close(),
    navigation = controller.navigate();
  await Promise.resolve();
  expect(controller.snapshot()).toMatchObject({ visible: true, pending: true });
  port.gate.resolve(undefined);
  await Promise.all([close, navigation]);
  expect(port.calls.filter((call) => call.kind === "stop")).toHaveLength(1);
  expect(controller.snapshot()).toMatchObject({ visible: false, frameUrl: null });
});

it("ignores duplicate and regressive statuses; observation never mounts a frame", async () => {
  const { port, controller } = setup();
  port.launchState = "starting";
  await controller.see(() => fakeCandidate);
  controller.observe(fakeStatus());
  controller.observe(fakeStatus("ready"));
  expect(controller.snapshot().frameUrl).toBeNull();
  await controller.refresh(); // Older starting details cannot regress accepted ready status.
  expect(controller.snapshot().status?.state).toBe("ready");
  controller.observe(fakeStatus("stopping", "closed"));
  controller.observe(fakeStatus("ready"));
  expect(controller.snapshot().status?.state).toBe("stopping");
});
it("suppresses stale details during Close and a source/run change", async () => {
  const { port, controller } = setup();
  await controller.see(() => fakeCandidate);
  const pending = deferred<PreviewDetails>();
  vi.spyOn(port, "details").mockReturnValue(pending.promise);
  const refresh = controller.refresh();
  await controller.setIdentity({ run: "run-new", source: "live-b" });
  pending.resolve({ status: fakeStatus("ready"), url: port.frameUrl });
  await refresh;
  expect(controller.snapshot()).toMatchObject({ visible: false, owned: false, frameUrl: null });
});
it("a late renewal cannot revive a stopped preview", async () => {
  const { port, controller } = setup();
  await controller.see(() => fakeCandidate);
  const pending = deferred<PreviewStatus>();
  vi.spyOn(port, "renew").mockReturnValue(pending.promise);
  await vi.advanceTimersByTimeAsync(10_000);
  await controller.close();
  pending.resolve(fakeStatus("ready"));
  await vi.advanceTimersByTimeAsync(1);
  expect(controller.snapshot()).toMatchObject({ visible: false, owned: false, frameUrl: null });
});
it("bounds lost stop delivery and relies on fake authority lease expiry", async () => {
  const { port, controller } = setup();
  await controller.see(() => fakeCandidate);
  port.stopMode = "lost";
  const closing = controller.close();
  await vi.advanceTimersByTimeAsync(5_000);
  await closing;
  expect(controller.snapshot()).toMatchObject({
    visible: true,
    uncertain: true,
    frameUrl: port.frameUrl,
  });
  await vi.advanceTimersByTimeAsync(30_000);
  expect(port.publicStatus()?.reason).toBe("lease_expired");
  expect(port.calls.filter((call) => call.kind === "renew")).toHaveLength(0);
});
it.each(["reject", "deadline"])(
  "keeps a %s start visible until verified closure and blocks retry",
  async (mode) => {
    const { port, controller } = setup();
    await port.start(fakeCandidate, new AbortController().signal);
    const start = vi.spyOn(port, "start");
    if (mode === "reject") start.mockRejectedValueOnce(new Error("In use"));
    else start.mockReturnValueOnce(deferred<Awaited<ReturnType<PreviewPort["start"]>>>().promise);
    const opening = controller.see(() => fakeCandidate);
    if (mode === "deadline") await vi.advanceTimersByTimeAsync(5_000);
    await opening;
    expect(controller.snapshot()).toMatchObject({ notice: "start_failed", owned: false });
    const calls = port.calls.length;
    await controller.close();
    expect(controller.snapshot().visible).toBe(true);
    const candidate = vi.fn(() => next);
    await controller.see(candidate);
    controller.observe({ ...fakeStatus("stopped", "lease_expired"), run_id: "other" });
    controller.observe(fakeStatus("ready"));
    controller.observe(fakeStatus("failed", "cleanup_unverified"));
    controller.observe({
      ...fakeStatus("failed", "cleanup_unverified"),
      preview_id: `preview-${"f".repeat(32)}`,
    });
    await controller.see(candidate);
    expect(candidate).not.toHaveBeenCalled();
    expect(port.calls).toHaveLength(calls);
    expect(controller.snapshot()).toMatchObject({ visible: true, notice: "start_failed" });
    await controller.close();
    port.expire();
    controller.observe(port.publicStatus() as PreviewStatus);
    expect(controller.snapshot()).toMatchObject({ visible: true, uncertain: false });
    await controller.see(candidate);
    expect(controller.snapshot()).toMatchObject({ owned: true, status: { agent_id: "agent-b" } });
    controller.dispose();
  },
);
it.each(["starting", "ready", "stopping", "ended"] as const)(
  "recovers a rejected agent-b start from agent-a's %s slot after the deadline",
  async (proof) => {
    const { port, controller } = setup();
    await port.start(fakeCandidate, new AbortController().signal);
    const start = vi.spyOn(port, "start");
    await controller.see(() => next);
    await controller.close();
    await controller.see(() => next);
    expect(controller.snapshot()).toMatchObject({ visible: true, notice: "start_failed" });
    if (proof === "ended") port.expire();
    else port.publish(proof, proof === "stopping" ? "closed" : null);
    controller.observe(port.publicStatus() as PreviewStatus);
    await controller.see(() => next);
    expect(start).toHaveBeenCalledTimes(1);
    expect(controller.snapshot().uncertain).toBe(true);
    await vi.advanceTimersByTimeAsync(5_000);
    controller.observe({ ...(port.publicStatus() as PreviewStatus), run_id: "other" });
    expect(controller.snapshot().uncertain).toBe(true);
    controller.observe(port.publicStatus() as PreviewStatus);
    expect(controller.snapshot()).toMatchObject({ owned: false, uncertain: false, notice: null });
    if (proof !== "ended") {
      await controller.see(() => next);
      expect(start).toHaveBeenCalledTimes(1);
      port.expire();
      controller.observe(port.publicStatus() as PreviewStatus);
    }
    await controller.see(() => next);
    expect(controller.snapshot()).toMatchObject({ owned: true, status: { agent_id: "agent-b" } });
    controller.dispose();
  },
);
it("drops tokenless start quarantine on a new run after a request never reached the server", async () => {
  const { port, controller } = setup();
  const start = vi.spyOn(port, "start").mockRejectedValueOnce(new Error("Offline"));
  await controller.see(() => next);
  await controller.setIdentity({ run: "new-run", source: "live-b" });
  await controller.see(() => ({ ...next, run_id: "new-run" }));
  expect(start).toHaveBeenCalledTimes(2);
  expect(controller.snapshot()).toMatchObject({ owned: true, status: { run_id: "new-run" } });
  controller.dispose();
});
it.each(["reject", "unverified", "lost"] as const)(
  "keeps authoritative status after %s stop and permits local Close after expiry",
  async (mode) => {
    const { port, controller } = setup();
    await controller.see(() => fakeCandidate);
    const ready = port.publicStatus();
    port.stopMode = mode;
    const closing = controller.close();
    await Promise.resolve();
    expect(controller.snapshot()).toMatchObject({ pending: true, status: ready });
    if (mode === "lost") await vi.advanceTimersByTimeAsync(5_000);
    await closing;
    expect(controller.snapshot()).toMatchObject({ status: ready, uncertain: true });
    if (mode === "unverified") {
      controller.observe(port.publicStatus() as PreviewStatus);
      expect(controller.snapshot()).toMatchObject({
        owned: true,
        frameUrl: port.frameUrl,
        status: { state: "failed", reason: "cleanup_unverified" },
      });
    }
    await vi.advanceTimersByTimeAsync(30_000);
    if (mode === "lost") await controller.refresh();
    else controller.observe(port.publicStatus() as PreviewStatus);
    expect(controller.snapshot()).toMatchObject({
      visible: true,
      owned: false,
      uncertain: false,
      frameUrl: null,
      status: { state: "stopped", reason: "lease_expired" },
    });
    const calls = port.calls.length;
    await controller.close();
    expect(controller.snapshot().visible).toBe(false);
    expect(port.calls).toHaveLength(calls);
    port.stopMode = "verified";
    await controller.see(() => next);
    expect(controller.snapshot()).toMatchObject({ owned: true, status: { agent_id: "agent-b" } });
    controller.dispose();
  },
);
it("does not restore quarantine when a pending stop rejects after authoritative expiry", async () => {
  const { port, controller } = setup();
  await controller.see(() => fakeCandidate);
  port.stopMode = "reject";
  port.gate = deferred<undefined>();
  const closing = controller.close();
  await Promise.resolve();
  port.expire();
  controller.observe(port.publicStatus() as PreviewStatus);
  expect(controller.snapshot()).toMatchObject({ owned: false, uncertain: false, frameUrl: null });
  port.gate.resolve(undefined);
  await closing;
  expect(controller.snapshot()).toMatchObject({ visible: false, uncertain: false });
  port.stopMode = "verified";
  await controller.see(() => next);
  expect(controller.snapshot()).toMatchObject({ owned: true, status: { agent_id: "agent-b" } });
  controller.dispose();
});
it.each(["dispose", "identity", "deadline"])(
  "drains a late start after %s without mounting or renewing",
  async (action) => {
    const fake = new PreviewFake(clock),
      pending = deferred<Awaited<ReturnType<PreviewPort["start"]>>>();
    const receipt = await fake.start(fakeCandidate, new AbortController().signal);
    const port: PreviewPort = {
      start: () => pending.promise,
      details: fake.details.bind(fake),
      stop: fake.stop.bind(fake),
      renew: fake.renew.bind(fake),
    };
    const controller = createPreviewController(port, clock, identity);
    const opening = controller.see(() => fakeCandidate);
    if (action === "dispose") controller.dispose();
    if (action === "identity") void controller.setIdentity({ run: "other", source: "other" });
    if (action === "deadline") await vi.advanceTimersByTimeAsync(5_000);
    pending.resolve(receipt);
    await opening;
    await vi.advanceTimersByTimeAsync(1);
    expect(controller.snapshot().frameUrl).toBeNull();
    expect(fake.calls.filter((call) => call.kind === "stop")).toHaveLength(1);
    expect(fake.calls.filter((call) => call.kind === "renew")).toHaveLength(0);
  },
);

it("serializes details per preview and does not let an old pending read block a replacement", async () => {
  const { port, controller } = setup();
  await controller.see(() => fakeCandidate);
  const old = deferred<PreviewDetails>();
  const read = vi.spyOn(port, "details").mockReturnValueOnce(old.promise);
  const pending = controller.refresh();
  await controller.refresh();
  expect(read).toHaveBeenCalledTimes(1);
  await controller.see(() => next);
  expect(controller.snapshot()).toMatchObject({
    owned: true,
    frameUrl: port.frameUrl,
    status: { agent_id: "agent-b" },
  });
  old.resolve({ status: fakeStatus("ready"), url: "https://stale.invalid" });
  await pending;
  expect(controller.snapshot().frameUrl).toBe(port.frameUrl);
  expect(controller.snapshot().status?.agent_id).toBe("agent-b");
});
it("rejects a trusted-boundary mistake that would leak a lease in the frame URL", async () => {
  const { port, controller } = setup();
  vi.spyOn(port, "details").mockResolvedValue({
    status: fakeStatus("ready"),
    url: "https://frame.invalid/fake-private-1",
  });
  await controller.see(() => fakeCandidate);
  expect(controller.snapshot()).toMatchObject({ uncertain: true, frameUrl: null });
  expect(JSON.stringify(controller.snapshot())).not.toContain("fake-private");
});
it.each(["starting", "ready", "stopping", "failed"] as const)(
  "an observer of %s cannot start after local Close and waits for expiry",
  async (state) => {
    const port = new PreviewFake(clock);
    await port.start(fakeCandidate, new AbortController().signal);
    port.publish(
      state,
      state === "starting" || state === "ready"
        ? null
        : state === "failed"
          ? "cleanup_unverified"
          : "closed",
    );
    const observer = createPreviewController(port, clock, identity);
    observer.observe(port.publicStatus() as PreviewStatus);
    await observer.close();
    expect(observer.snapshot().visible).toBe(false);
    await observer.see(() => next);
    expect(observer.snapshot()).toMatchObject({ visible: true, owned: false, frameUrl: null });
    expect(port.calls.map((call) => call.kind)).toEqual(["start"]);
    port.expire();
    observer.observe(port.publicStatus() as PreviewStatus);
    await observer.see(() => next);
    expect(observer.snapshot()).toMatchObject({ owned: true, status: { agent_id: "agent-b" } });
  },
);
