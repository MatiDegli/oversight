import { expect, it, vi } from "vitest";
import type { PreviewDetails, PreviewStatus, StartPreview } from "@contracts";
import { createPreviewAcceptancePort } from "./previewAcceptance";

const id = `preview-${"a".repeat(32)}`;
const lease = "A".repeat(43);
const candidate: StartPreview = {
  run_id: "run-20261103-000000",
  agent_id: "agent-1",
  revision: 1,
  head_commit: "head",
};
const status: PreviewStatus = {
  ...candidate,
  change_id: "change-1",
  preview_id: id,
  state: "ready",
  reason: null,
};
const signal = new AbortController().signal;
const validators = {
  status: vi.fn((value: unknown) => value as PreviewStatus),
  details: vi.fn((value: unknown) => value as PreviewDetails),
};
function setup(value: unknown, status = 200, headers = { "Cache-Control": "no-store" }) {
  const request = vi
    .fn<typeof fetch>()
    .mockResolvedValue(new Response(JSON.stringify(value), { status, headers }));
  return {
    request,
    port: createPreviewAcceptancePort("http://127.0.0.1:8710", validators, request),
  };
}
it("projects exact generated requests and supplies private bounded fetch policies", async () => {
  const { port, request } = setup({ preview_id: id, lease_token: lease }, 202);
  await expect(port.start({ ...candidate, extra: true } as StartPreview, signal)).resolves.toEqual({
    preview_id: id,
    lease_token: lease,
  });
  expect(request).toHaveBeenCalledTimes(1);
  const [url, init] = request.mock.calls[0] ?? [];
  expect(url instanceof URL ? url.href : null).toBe("http://127.0.0.1:8710/previews");
  expect(init).toMatchObject({
    method: "POST",
    body: JSON.stringify(candidate),
    cache: "no-store",
    credentials: "omit",
    redirect: "error",
    referrerPolicy: "no-referrer",
  });
  expect(init?.signal).toBeInstanceOf(AbortSignal);
});
it("uses the existing injected validators for details and renewal", async () => {
  const details = { status, url: "http://localhost:19800/" };
  const first = setup(details);
  await expect(first.port.details(id, signal)).resolves.toEqual(details);
  expect(validators.details).toHaveBeenCalledWith(details);
  const second = setup(status);
  await expect(
    second.port.renew(id, { lease_token: lease, extra: true } as { lease_token: string }, signal),
  ).resolves.toEqual(status);
  expect(validators.status).toHaveBeenCalledWith(status);
  expect(second.request.mock.calls[0]?.[1]?.body).toBe(JSON.stringify({ lease_token: lease }));
});
it.each([
  "https://127.0.0.1:19800/",
  "http://127.0.0.1:19800/",
  "http://127.0.0.1:8710/",
  "http://127.0.0.1:19800/path",
  "http://127.0.0.1:19800/?token=x",
])("refuses unsafe frame destination %s", async (url) => {
  const { port } = setup({ status, url });
  await expect(port.details(id, signal)).rejects.toThrow();
});
it.each(["ready", "stopping", "failed"] as const)(
  "does not certify unverified stop %s",
  async (state) => {
    const { port } = setup({
      ...status,
      state,
      reason: state === "ready" ? null : "cleanup_unverified",
    });
    await expect(port.stop(id, { lease_token: lease }, signal)).rejects.toThrow(
      "closure is unverified",
    );
  },
);
it("accepts verified terminal stop and refuses malformed identities before fetch", async () => {
  const terminal = { ...status, state: "stopped", reason: "closed" };
  const { port, request } = setup(terminal);
  await expect(port.stop(id, { lease_token: lease }, signal)).resolves.toEqual(terminal);
  await expect(port.details("../files", signal)).rejects.toThrow();
  await expect(port.renew(id, { lease_token: "bad" }, signal)).rejects.toThrow();
  expect(request).toHaveBeenCalledTimes(1);
});
it.each([403, 409, 422, 500, 501, 503, 302])(
  "hides error bodies for %s without retry",
  async (code) => {
    const { port, request } = setup({ error: `PRIVATE_${lease}` }, code);
    await expect(port.details(id, signal)).rejects.toThrow("Preview request could not be verified");
    expect(request).toHaveBeenCalledTimes(1);
  },
);
it("refuses missing no-store, extra receipt keys and validator failures", async () => {
  await expect(
    setup({ preview_id: id, lease_token: lease, url: "secret" }, 202).port.start(candidate, signal),
  ).rejects.toThrow("Invalid preview receipt");
  await expect(
    setup(status, 200, { "Cache-Control": "public" }).port.renew(
      id,
      { lease_token: lease },
      signal,
    ),
  ).rejects.toThrow("could not be verified");
  const { request } = setup(status);
  const port = createPreviewAcceptancePort(
    "http://127.0.0.1:8710",
    {
      ...validators,
      status() {
        throw new Error("Invalid preview input");
      },
    },
    request,
  );
  await expect(port.renew(id, { lease_token: lease }, signal)).rejects.toThrow(
    "Invalid preview input",
  );
});
