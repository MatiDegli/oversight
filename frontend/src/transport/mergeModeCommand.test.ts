import { expect, it, vi } from "vitest";
import { CommandError, createCommandClient } from "./commands";

it("sends one PUT with the observed mode revision", async () => {
  const body = { mode: "merge_when_safe", revision: 7 } as const;
  const response = { ...body, revision: 8 };
  const request = vi.fn<typeof fetch>().mockResolvedValue(Response.json(response));
  expect(await createCommandClient(request).setMergeMode(body)).toEqual(response);
  expect(request).toHaveBeenCalledExactlyOnceWith("/merge-mode", {
    method: "PUT",
    signal: undefined,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
});
it.each([
  [409, "Cross-review is off"],
  [501, "Setting the merge mode is not available in a replay"],
])("keeps the %i server refusal in CommandError", async (status, message) => {
  const request = vi
    .fn<typeof fetch>()
    .mockResolvedValue(Response.json({ error: { code: "wrong_state", message } }, { status }));
  const result = createCommandClient(request).setMergeMode({
    mode: "merge_when_safe",
    revision: 0,
  });
  await expect(result).rejects.toBeInstanceOf(CommandError);
  await expect(result).rejects.toMatchObject({ status, message });
});
