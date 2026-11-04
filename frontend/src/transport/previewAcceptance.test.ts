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
function setup(
  value: unknown,
  status = 200,
  headers = { "Cache-Control": "no-store" },
  base = "http://127.0.0.1:8710",
) {
  const request = vi
    .fn<typeof fetch>()
    .mockResolvedValue(new Response(JSON.stringify(value), { status, headers }));
  return {
    request,
    port: createPreviewAcceptancePort(base, validators, request),
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
const hosts = ["127.0.0.1", "localhost"] as const;
const opposite = (host: (typeof hosts)[number]) =>
  host === "127.0.0.1" ? "localhost" : "127.0.0.1";
const unsafe = (host: string, port: string) => [
  `https://${host}:${port}/`,
  `http://${host}/`,
  `http://${host}:80/`,
  `http://${host}:${port}/path`,
  `http://${host}:${port}/?token=x`,
  `http://${host}:${port}/#frame`,
  `http://user@${host}:${port}/`,
  `http://:password@${host}:${port}/`,
  `http://user:password@${host}:${port}/`,
  `http://example.com:${port}/`,
  `http://[::1]:${port}/`,
];
it.each(hosts.flatMap((host) => unsafe(host, "8710")))("refuses unsafe base %s", (base) => {
  const request = vi.fn<typeof fetch>();
  expect(() => createPreviewAcceptancePort(base, validators, request)).toThrow();
  expect(request).not.toHaveBeenCalled();
});
it.each(
  hosts.flatMap((host) =>
    [
      ...unsafe(opposite(host), "19800"),
      `http://${host}:19800/`,
      `http://${host}:8710/`,
      `http://${opposite(host)}:8710/`,
    ].map((url) => ({ base: `http://${host}:8710`, url })),
  ),
)("refuses unsafe frame $url for $base", async ({ base, url }) => {
  const { port } = setup({ status, url }, 200, undefined, base);
  await expect(port.details(id, signal)).rejects.toThrow();
});
it.each(hosts)("uses supplied %s base despite ambient location changes", async (host) => {
  const base = `http://${host}:8710`;
  const details = { status, url: `http://${opposite(host)}:19800/` };
  const location = { origin: `http://${opposite(host)}:9999` };
  vi.stubGlobal("location", location);
  vi.stubGlobal("window", { location });
  try {
    const { port, request } = setup(details, 200, undefined, base);
    location.origin = "https://ambient.invalid";
    await expect(port.details(id, signal)).resolves.toEqual(details);
    expect(request).toHaveBeenCalledTimes(1);
    const [url] = request.mock.calls[0] ?? [];
    expect(url instanceof URL ? url.href : null).toBe(`${base}/previews/${id}`);
  } finally {
    vi.unstubAllGlobals();
  }
});
it.each(hosts)("preserves null details for %s base", async (host) => {
  const details = { status, url: null };
  const { port } = setup(details, 200, undefined, `http://${host}:8710`);
  await expect(port.details(id, signal)).resolves.toEqual(details);
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
