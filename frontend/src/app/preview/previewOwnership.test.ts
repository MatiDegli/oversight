import { afterEach, expect, it, vi } from "vitest";
import { createPreviewOwnershipStore, type PreviewOwnershipStore } from "./previewOwnership";
import { createPreviewController } from "./previewController";
import { deferred, fakeCandidate, fakeStatus, PreviewFake, PreviewFakeClock } from "./previewFake";
import type { PreviewPort } from "./previewPort";
import type { PreviewDetails } from "./previewTypes";

const origin = "http://127.0.0.1:8710";
const identity = { run: fakeCandidate.run_id, source: "live" };
const candidate = { ...fakeCandidate, change_id: "change-agent-a" };
const token = "R".repeat(43);
const record = { identity, candidate, preview_id: fakeStatus().preview_id, lease_token: token };
function storage() {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
    removeItem: (key: string) => {
      data.delete(key);
    },
  };
}
function setup() {
  const disk = storage();
  const store = createPreviewOwnershipStore(origin, () => disk);
  const clock = new PreviewFakeClock();
  const fake = new PreviewFake();
  let privateToken = "";
  const port: PreviewPort = {
    async start(input, signal) {
      const receipt = await fake.start(input, signal);
      privateToken = receipt.lease_token;
      return { ...receipt, lease_token: token };
    },
    details: fake.details.bind(fake),
    renew: (id, body, signal) => {
      if (body.lease_token !== token) return Promise.reject(new Error("Refused"));
      return fake.renew(id, { lease_token: privateToken }, signal);
    },
    stop: (id, body, signal) => {
      if (body.lease_token !== token) return Promise.reject(new Error("Refused"));
      return fake.stop(id, { lease_token: privateToken }, signal);
    },
  };
  const controller = () => createPreviewController(port, clock, identity, store);
  return { disk, store, clock, fake, port, controller };
}
afterEach(() => vi.restoreAllMocks());

it("stores only minimal origin-scoped recovery data and uses compare-and-clear", () => {
  const disk = storage(),
    store = createPreviewOwnershipStore(origin, () => disk);
  store.save(record);
  expect(store.load(identity)).toEqual(record);
  const raw = [...disk.data.values()][0];
  expect(raw).not.toMatch(/url|frame|https:/);
  expect(
    createPreviewOwnershipStore("http://127.0.0.1:8711", () => disk).load(identity),
  ).toBeNull();
  const newer = { ...record, preview_id: `preview-${"f".repeat(32)}` };
  store.save(newer);
  store.clear(record);
  expect(store.load(identity)).toEqual(newer);
  store.clear(newer);
  expect(disk.data.size).toBe(0);
});
it.each([
  "broken",
  "null",
  "[]",
  "{}",
  JSON.stringify({ ...record, lease_token: "short" }),
  JSON.stringify({ ...record, preview_id: "../other" }),
  JSON.stringify({ ...record, candidate: { ...candidate, revision: 0 } }),
  JSON.stringify({ ...record, candidate: { ...candidate, run_id: "other" } }),
  JSON.stringify({ ...record, url: "http://frame.invalid" }),
])("clears invalid storage without returning authority: %s", (raw) => {
  const disk = storage(),
    store = createPreviewOwnershipStore(origin, () => disk);
  store.save(record);
  const key = [...disk.data.keys()][0];
  if (!key) throw new Error("Missing fixture key");
  disk.data.set(key, raw);
  expect(store.load(identity)).toBeNull();
  expect(disk.data.size).toBe(0);
});
it.each([
  { run: "other", source: "live" },
  { ...identity, source: "replay:1" },
])("clears mismatched identity %j", (next) => {
  const disk = storage(),
    store = createPreviewOwnershipStore(origin, () => disk);
  store.save(record);
  expect(store.load(next)).toBeNull();
  expect(disk.data.size).toBe(0);
});
it("waits for a bootstrap run before reading or clearing storage", () => {
  const disk = storage(),
    store = createPreviewOwnershipStore(origin, () => disk);
  store.save(record);
  expect(store.load({ run: "", source: "live" })).toBeNull();
  expect(store.load(identity)).toEqual(record);
});
it.each(["access", "getItem", "setItem", "removeItem"])(
  "tolerates throwing %s storage",
  (operation) => {
    const disk = storage();
    const store = createPreviewOwnershipStore(origin, () => {
      if (operation === "access") throw new Error("Unavailable");
      return {
        ...disk,
        [operation]: () => {
          throw new Error("Unavailable");
        },
      };
    });
    expect(() => {
      store.save(record);
      store.load(identity);
      store.clear(record);
    }).not.toThrow();
  },
);

it("recovers a lost unload using the same ID, renews hidden and reveals only on activation", async () => {
  const { controller, fake, port, store, clock } = setup();
  const first = controller();
  await first.see(() => candidate);
  fake.stopMode = "lost";
  first.dispose();
  expect(store.load(identity)).toEqual(record);
  const recovered = controller();
  const renew = vi.spyOn(port, "renew");
  await recovered.recover(() => candidate);
  expect(renew).toHaveBeenCalledWith(
    record.preview_id,
    { lease_token: token },
    expect.any(AbortSignal),
  );
  expect(recovered.snapshot()).toMatchObject({ owned: true, visible: false, frameUrl: null });
  clock.advance(10_000);
  await recovered.recover(() => candidate);
  expect(renew).toHaveBeenCalledTimes(2);
  await recovered.see(() => candidate);
  expect(recovered.snapshot()).toMatchObject({
    owned: true,
    visible: true,
    frameUrl: fake.frameUrl,
  });
  expect(fake.calls.filter((c) => c.kind === "start")).toHaveLength(1);
  expect(JSON.stringify(recovered.snapshot())).not.toContain(token);
  fake.stopMode = "verified";
  await recovered.close();
  expect(store.load(identity)).toBeNull();
  recovered.dispose();
});
it("can stop hidden recovered ownership without ever obtaining a frame", async () => {
  const { controller, fake, store } = setup();
  await controller().see(() => candidate);
  const recovered = controller();
  await recovered.recover(() => candidate);
  await recovered.close();
  expect(recovered.snapshot()).toMatchObject({ owned: false, visible: false, frameUrl: null });
  expect(fake.calls.filter((c) => c.kind === "stop")).toHaveLength(1);
  expect(store.load(identity)).toBeNull();
});
it.each([
  "closed",
  "lease_expired",
  "lifetime_expired",
  "launch_failed",
  "cleanup_unverified",
] as const)("clears terminal recovery %s and never renews or retries", async (reason) => {
  const { controller, fake, port, store } = setup();
  await controller().see(() => candidate);
  fake.publish(
    reason === "launch_failed" || reason === "cleanup_unverified" ? "failed" : "stopped",
    reason,
  );
  const renew = vi.spyOn(port, "renew"),
    stop = vi.spyOn(port, "stop");
  const recovered = controller();
  await recovered.recover(() => candidate);
  await recovered.recover(() => candidate);
  await recovered.close();
  expect(store.load(identity)).toBeNull();
  expect(renew).not.toHaveBeenCalled();
  expect(stop).not.toHaveBeenCalled();
  expect(recovered.snapshot()).toMatchObject({
    owned: false,
    frameUrl: null,
    uncertain: reason === "cleanup_unverified",
  });
});
it.each(["missing", "mismatch", "candidate", "rejected", "terminal-renew"])(
  "discards %s recovery permanently",
  async (mode) => {
    const { controller, fake, port, store, clock } = setup();
    const first = controller();
    await first.see(() => candidate);
    fake.stopMode = "lost";
    first.dispose();
    const details = vi.spyOn(port, "details"),
      renew = vi.spyOn(port, "renew");
    if (mode === "missing") details.mockRejectedValue(new Error("404"));
    if (mode === "mismatch")
      details.mockResolvedValue({ status: { ...fakeStatus("ready"), revision: 2 }, url: null });
    if (mode === "rejected") renew.mockRejectedValue(new Error("403"));
    if (mode === "terminal-renew") renew.mockResolvedValue(fakeStatus("stopped", "lease_expired"));
    const recovered = controller();
    await recovered.recover(() =>
      mode === "candidate" ? { ...candidate, revision: 2 } : candidate,
    );
    expect(store.load(identity)).toBeNull();
    expect(recovered.snapshot()).toMatchObject({ owned: false, frameUrl: null });
    const count = renew.mock.calls.length;
    clock.advance(60_000);
    await recovered.recover(() => candidate);
    expect(renew).toHaveBeenCalledTimes(count);
    recovered.dispose();
  },
);
it.each(["dispose", "identity", "candidate"])(
  "fences %s while recovery details are pending",
  async (action) => {
    const { controller, port, store } = setup();
    await controller().see(() => candidate);
    const pending = deferred<PreviewDetails>();
    vi.spyOn(port, "details").mockReturnValue(pending.promise);
    const renew = vi.spyOn(port, "renew"),
      stop = vi.spyOn(port, "stop");
    const recovered = controller();
    let current = candidate;
    const recovery = recovered.recover(() => current);
    if (action === "dispose") recovered.dispose();
    if (action === "identity") await recovered.setIdentity({ run: "other", source: "live" });
    if (action === "candidate") current = { ...candidate, revision: 2 };
    pending.resolve({ status: fakeStatus("ready"), url: "http://127.0.0.1:19800/" });
    await recovery;
    expect(renew).not.toHaveBeenCalled();
    expect(stop).not.toHaveBeenCalled();
    expect(recovered.snapshot()).toMatchObject({ owned: false, frameUrl: null });
    if (action !== "dispose") expect(store.load(identity)).toBeNull();
  },
);
it("preserves mounted owner authority for verified Close after storage or renewal failure", async () => {
  const { fake, port, clock } = setup();
  const failing: PreviewOwnershipStore = {
    load: () => {
      throw new Error("Unavailable");
    },
    save: () => {
      throw new Error("Unavailable");
    },
    clear: () => {
      throw new Error("Unavailable");
    },
  };
  const owner = createPreviewController(port, clock, identity, failing);
  await owner.see(() => candidate);
  fake.renewalRejects = true;
  clock.advance(10_000);
  await Promise.resolve();
  await owner.close();
  expect(owner.snapshot()).toMatchObject({ owned: false, frameUrl: null });
});

it("clears a delivered unload on the next load without renewal or another start", async () => {
  const { controller, port, store, fake } = setup();
  const first = controller();
  await first.see(() => candidate);
  const stop = vi.spyOn(port, "stop");
  first.dispose();
  const next = controller();
  await next.recover(() => candidate);
  expect(store.load(identity)).toBeNull();
  expect(next.snapshot()).toMatchObject({ owned: false, frameUrl: null });
  expect(fake.calls.filter((call) => call.kind === "renew")).toHaveLength(0);
  expect(fake.calls.filter((call) => call.kind === "start")).toHaveLength(1);
  expect(stop).toHaveBeenCalledTimes(1);
});
it("does not renew stale ready details after a terminal arrival during recovery", async () => {
  const { controller, port, fake, store } = setup();
  const first = controller();
  await first.see(() => candidate);
  fake.stopMode = "lost";
  first.dispose();
  const pending = deferred<PreviewDetails>();
  vi.spyOn(port, "details").mockReturnValue(pending.promise);
  const renew = vi.spyOn(port, "renew");
  const next = controller();
  const recovery = next.recover(() => candidate);
  next.observe(fakeStatus("failed", "cleanup_unverified"));
  pending.resolve({ status: fakeStatus("ready"), url: null });
  await recovery;
  expect(renew).not.toHaveBeenCalled();
  expect(store.load(identity)).toBeNull();
  expect(next.snapshot()).toMatchObject({ owned: false, uncertain: true, frameUrl: null });
});
it("clears persistence for cleanup_unverified while a mounted owner retains verified Close authority", async () => {
  const { controller, store, fake } = setup();
  const owner = controller();
  await owner.see(() => candidate);
  owner.observe(fakeStatus("failed", "cleanup_unverified"));
  expect(store.load(identity)).toBeNull();
  expect(owner.snapshot().owned).toBe(true);
  await owner.close();
  expect(fake.calls.filter((call) => call.kind === "stop")).toHaveLength(1);
  expect(owner.snapshot()).toMatchObject({ owned: false, visible: false });
});

it("keeps independent-store observers read-only across dismissal, arrivals, recovery and disposal", async () => {
  const { controller, port, clock, fake, store } = setup();
  const first = controller();
  await first.see(() => candidate);
  fake.stopMode = "lost";
  first.dispose();
  const owner = controller();
  await owner.recover(() => candidate);
  const observerStore = createPreviewOwnershipStore(origin, () => storage());
  const observer = createPreviewController(port, clock, identity, observerStore);
  await observer.recover(() => candidate);
  const before = fake.calls.length;
  for (let count = 0; count < 3; count++) {
    observer.observe(fake.publicStatus() ?? fakeStatus("ready"));
    await observer.see(() => ({ ...candidate, revision: 2 }));
    await observer.refresh();
    expect(observer.snapshot()).toMatchObject({ owned: false, frameUrl: null });
    await observer.close();
    expect(observer.snapshot().visible).toBe(false);
  }
  expect(fake.calls).toHaveLength(before);
  observer.dispose();
  expect(fake.calls).toHaveLength(before);
  expect(store.load(identity)).toEqual(record);
  fake.stopMode = "verified";
  await owner.close();
  expect(store.load(identity)).toBeNull();
});
it.each(["closed", "lease_expired", "launch_failed"] as const)(
  "an observer cannot reopen the same candidate after public %s, but a new candidate remains eligible",
  async (reason) => {
    const { controller, port, fake, clock } = setup();
    const owner = controller();
    await owner.see(() => candidate);
    fake.expire();
    const observer = createPreviewController(
      port,
      clock,
      identity,
      createPreviewOwnershipStore(origin, () => storage()),
    );
    observer.observe(fakeStatus(reason === "launch_failed" ? "failed" : "stopped", reason));
    await observer.close();
    const starts = fake.calls.filter((call) => call.kind === "start").length;
    await observer.see(() => candidate);
    expect(fake.calls.filter((call) => call.kind === "start")).toHaveLength(starts);
    expect(observer.snapshot()).toMatchObject({ owned: false, frameUrl: null });
    await observer.see(() => ({ ...candidate, revision: 2, head_commit: "new-head" }));
    expect(observer.snapshot()).toMatchObject({ owned: true, status: { revision: 2 } });
    observer.dispose();
  },
);
it("terminal recovery cannot be revived by activation or repeated public ready arrivals", async () => {
  const { controller, port, fake, clock } = setup();
  await controller().see(() => candidate);
  fake.expire();
  const recovered = controller();
  await recovered.recover(() => candidate);
  recovered.observe(fakeStatus("ready"));
  await recovered.see(() => candidate);
  await recovered.close();
  clock.advance(10_000);
  expect(fake.calls.filter((call) => call.kind === "start")).toHaveLength(1);
  expect(fake.calls.filter((call) => call.kind === "renew")).toHaveLength(0);
  expect(recovered.snapshot()).toMatchObject({ owned: false, frameUrl: null });
  const stop = vi.spyOn(port, "stop");
  recovered.dispose();
  expect(stop).not.toHaveBeenCalled();
});

it("a recovered mounted owner retains normal verified-Close and explicit new-start behavior", async () => {
  const { controller, fake, port, clock } = setup();
  const first = controller();
  await first.see(() => candidate);
  fake.stopMode = "lost";
  first.dispose();
  const owner = controller();
  owner.observe(fakeStatus("ready"));
  await owner.recover(() => candidate);
  await owner.see(() => candidate);
  fake.renewalRejects = true;
  clock.advance(10_000);
  await Promise.resolve();
  await Promise.resolve();
  expect(owner.snapshot().owned).toBe(true);
  fake.stopMode = "verified";
  await owner.close();
  fake.renewalRejects = false;
  await owner.see(() => candidate);
  expect(owner.snapshot()).toMatchObject({ owned: true, frameUrl: fake.frameUrl });
  expect(fake.calls.filter((call) => call.kind === "start")).toHaveLength(2);
  const stop = vi.spyOn(port, "stop");
  owner.dispose();
  expect(stop).toHaveBeenCalledTimes(1);
});

it("hidden recovered ownership cannot obtain frame details through a direct refresh", async () => {
  const { controller, fake, port } = setup();
  const first = controller();
  await first.see(() => candidate);
  fake.stopMode = "lost";
  first.dispose();
  const owner = controller();
  await owner.recover(() => candidate);
  const details = vi.spyOn(port, "details");
  await owner.refresh();
  expect(owner.snapshot()).toMatchObject({ owned: true, visible: false, frameUrl: null });
  expect(details).not.toHaveBeenCalled();
  fake.stopMode = "verified";
  await owner.close();
});
it.each(["stopped", "failed"] as const)(
  "a hidden owner receiving %s after recovery cannot revive its candidate",
  async (state) => {
    const { controller, fake, clock } = setup();
    const first = controller();
    await first.see(() => candidate);
    fake.stopMode = "lost";
    first.dispose();
    const owner = controller();
    await owner.recover(() => candidate);
    fake.expire();
    owner.observe(fakeStatus(state, state === "failed" ? "launch_failed" : "closed"));
    await owner.see(() => candidate);
    clock.advance(10_000);
    expect(fake.calls.filter((call) => call.kind === "start")).toHaveLength(1);
    expect(owner.snapshot()).toMatchObject({ owned: false, frameUrl: null });
  },
);
it("clears a valid stored record with different whitespace and property order", () => {
  const disk = storage(),
    store = createPreviewOwnershipStore(origin, () => disk);
  store.save(record);
  const key = [...disk.data.keys()][0];
  if (!key) throw new Error("Missing fixture key");
  disk.data.set(
    key,
    JSON.stringify(
      {
        lease_token: record.lease_token,
        preview_id: record.preview_id,
        candidate: record.candidate,
        identity: record.identity,
      },
      null,
      2,
    ),
  );
  const saved = store.load(identity);
  expect(saved).toEqual(record);
  if (saved) store.clear(saved);
  expect(disk.data.size).toBe(0);
});

it("Close during recovery details stops the matching owner without renewing or revealing it", async () => {
  const { controller, fake, port, store } = setup();
  const first = controller();
  await first.see(() => candidate);
  fake.stopMode = "lost";
  first.dispose();
  fake.stopMode = "verified";
  const pending = deferred<PreviewDetails>();
  vi.spyOn(port, "details").mockReturnValue(pending.promise);
  const renew = vi.spyOn(port, "renew"),
    stop = vi.spyOn(port, "stop");
  const owner = controller();
  const recovering = owner.recover(() => candidate);
  const closing = owner.close();
  pending.resolve({ status: fakeStatus("ready"), url: fake.frameUrl });
  await Promise.all([recovering, closing]);
  expect(renew).not.toHaveBeenCalled();
  expect(stop).toHaveBeenCalledTimes(1);
  expect(store.load(identity)).toBeNull();
  expect(owner.snapshot()).toMatchObject({ owned: false, visible: false, frameUrl: null });
});

it.each(["reject", "unverified"] as const)(
  "discards a hidden recovered owner's %s stop authority while retaining quarantine",
  async (mode) => {
    const { controller, fake, port, store } = setup();
    const first = controller();
    await first.see(() => candidate);
    fake.stopMode = "lost";
    first.dispose();
    const owner = controller();
    await owner.recover(() => candidate);
    fake.stopMode = mode;
    await owner.close();
    expect(store.load(identity)).toBeNull();
    expect(owner.snapshot()).toMatchObject({ owned: false, uncertain: true, frameUrl: null });
    const stop = vi.spyOn(port, "stop");
    await owner.close();
    owner.dispose();
    expect(stop).not.toHaveBeenCalled();
  },
);

it("clears a late verified stop receipt after the local Close deadline without duplicate unload", async () => {
  const { controller, port, clock, store } = setup();
  const owner = controller();
  await owner.see(() => candidate);
  const pending = deferred<ReturnType<typeof fakeStatus>>();
  const stop = vi.spyOn(port, "stop").mockReturnValue(pending.promise);
  const closing = owner.close();
  await Promise.resolve();
  clock.advance(5_000);
  await closing;
  expect(store.load(identity)).toEqual(record);
  owner.dispose();
  expect(stop).toHaveBeenCalledTimes(1);
  pending.resolve(fakeStatus("stopped", "closed"));
  await Promise.resolve();
  await Promise.resolve();
  expect(store.load(identity)).toBeNull();
});
