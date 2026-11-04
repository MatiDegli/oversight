import { expect, it, vi } from "vitest";
import { createCommandClient } from "./commands";

it("reads settings and sends only the supplied patch to the live endpoint", async () => {
  const request = vi
    .fn<typeof fetch>()
    .mockImplementation(() => Promise.resolve(new Response("{}")));
  const client = createCommandClient(request);
  await client.getSettings();
  expect(request).toHaveBeenLastCalledWith("/settings", { method: "GET", signal: undefined });
  await client.updateSettings({ values: { OVERSIGHT_MAX_AGENTS: "8" } });
  expect(request).toHaveBeenLastCalledWith("/settings", {
    method: "PUT",
    signal: undefined,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values: { OVERSIGHT_MAX_AGENTS: "8" } }),
  });
});
