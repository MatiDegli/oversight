import { expect, it, vi } from "vitest";
import { CommandError, createCommandClient } from "./commands";

const body = { resolution_seq: 20, merge_commit: "4ca5596", main_commit: "9c234aa" };
it("POSTs the exact target and returns undefined for an empty 202", async () => {
  const request = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 202 }));
  await expect(
    createCommandClient(request).revertChange("change/1", body),
  ).resolves.toBeUndefined();
  expect(request).toHaveBeenCalledExactlyOnceWith("/changes/change%2F1/revert", {
    method: "POST",
    signal: undefined,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
});
it.each([
  [404, "there is no rules resolution 20 for change 'change-1'"],
  [409, "Revert target is already reserved or consumed"],
] as const)("keeps the %i refusal", async (status, message) => {
  const request = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json({ error: { code: "wrong_state", message } }, { status }));
  const result = createCommandClient(request).revertChange("change-1", body);
  await expect(result).rejects.toBeInstanceOf(CommandError);
  await expect(result).rejects.toMatchObject({ status, message });
});
