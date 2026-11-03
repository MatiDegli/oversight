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
