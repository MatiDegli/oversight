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
