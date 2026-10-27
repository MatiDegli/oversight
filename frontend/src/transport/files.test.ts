import { expect, it, vi } from "vitest";

import { CommandError, createCommandClient } from "./commands";

it("reads UTF-8 text on main and in a worktree with encoded paths and the optional agent query", async () => {
  const file = {
    path: "docs/a & b.toml",
    content: "name = 'ñ'",
    hash: "read-hash",
    agent_id: null,
  };
  const request = vi
    .fn<typeof fetch>()
    .mockImplementation(() => Promise.resolve(Response.json(file)));
  const client = createCommandClient(request);
  await expect(client.getFile(file.path)).resolves.toEqual(file);
  await expect(client.getFile(file.path, "agent/1")).resolves.toEqual(file);
  expect(request.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
    ["/files?path=docs%2Fa+%26+b.toml", "GET"],
    ["/files?path=docs%2Fa+%26+b.toml&agent=agent%2F1", "GET"],
  ]);
});

it("PUT sends the saved base hash and text without an agent on main, and returns FileSaved", async () => {
  const body = { path: "pyproject.toml", content: "name = 'ñ'", base_hash: "read-hash" };
  const answer = { path: body.path, hash: "saved-hash" };
  const request = vi.fn<typeof fetch>().mockResolvedValue(Response.json(answer));
  await expect(createCommandClient(request).saveFile(body)).resolves.toEqual(answer);
  expect(request).toHaveBeenCalledExactlyOnceWith("/files", {
    method: "PUT",
    signal: undefined,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
});

it.each(["stale_file", "wrong_state", "invalid_path"])(
  "keeps the server's %s error for the file view",
  async (code) => {
    const request = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        Response.json(
          { error: { code, message: "The file moved. Reload to continue." } },
          { status: 409 },
        ),
      );
    const promise = createCommandClient(request).saveFile({
      path: "a.py",
      content: "draft",
      base_hash: "old",
    });
    await expect(promise).rejects.toBeInstanceOf(CommandError);
    await expect(promise).rejects.toMatchObject({
      code,
      message: "The file moved. Reload to continue.",
    });
  },
);
