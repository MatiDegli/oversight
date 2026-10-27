import type { SaveFile } from "@contracts";
import { expect, it, vi } from "vitest";

import { createFileSession } from "../app";
import { CommandError } from "../transport";
import { FileDocument, fileKey } from "./fileDocument";
import { modelDouble } from "./fileModel.testSupport";

vi.mock("./monaco", () => ({ monaco: { editor: { EndOfLineSequence: { LF: 0, CRLF: 1 } } } }));

it("keeps main and both agents' drafts and concurrent saves separate for the same path", async () => {
  const resolvers: (() => void)[] = [];
  const saveFile = vi.fn(
    (body: SaveFile) =>
      new Promise<{ path: string; hash: string }>((resolve) => {
        resolvers.push(() => {
          resolve({ path: body.path, hash: "saved" });
        });
      }),
  );
  const files = createFileSession({
    getFile: (path, agent) =>
      Promise.resolve({
        path,
        agent_id: agent ?? null,
        content: "base\r\n",
        hash: `hash-${agent ?? "main"}`,
      }),
    saveFile,
  });
  const documents = [null, "agent-1", "agent-2"].map((agentId) => {
    const model = modelDouble();
    const doc = new FileDocument(
      { path: "same.py", agentId, startLine: 1, endLine: 1 },
      model,
      files,
      vi.fn(),
      undefined,
      agentId !== null,
    );
    return { doc, model, agentId };
  });
  await Promise.all(documents.map(({ doc }) => doc.load()));
  for (const { doc, model, agentId } of documents) {
    model.setValue(`edited by ${agentId ?? "main"}\r\n`);
    doc.save();
  }
  await Promise.resolve();
  expect(saveFile).toHaveBeenCalledTimes(3);
  expect(files.inFlightSaves.size).toBe(3);
  for (const { agentId } of documents) {
    expect(saveFile).toHaveBeenCalledWith({
      path: "same.py",
      content: `edited by ${agentId ?? "main"}\r\n`,
      base_hash: `hash-${agentId ?? "main"}`,
      ...(agentId ? { agent_id: agentId } : {}),
    });
  }
  const pending = [...files.inFlightSaves.values()];
  resolvers.forEach((resolve) => {
    resolve();
  });
  await Promise.all(pending);
  for (const { doc, agentId } of documents) {
    expect(doc.state.phase).toBe("clean");
    expect(files.saved.get(fileKey("same.py", agentId))?.agent_id).toBe(agentId);
    doc.dispose();
  }
});

it.each(["stale_file", "wrong_state"] as const)(
  "keeps the review model and base hash on %s, including across disposal",
  async (code) => {
    const saveFile = vi.fn().mockRejectedValue(new CommandError(409, code, "Wait for the agent."));
    const files = createFileSession({
      getFile: (path) =>
        Promise.resolve({ path, agent_id: "agent-1", content: "base", hash: "base-hash" }),
      saveFile,
    });
    const target = { path: "same.py", agentId: "agent-1", startLine: 1, endLine: 1 };
    const model = modelDouble();
    const doc = new FileDocument(target, model, files, vi.fn(), undefined, true);
    await doc.load();
    model.setValue("my text");
    doc.save();
    await expect(files.inFlightSaves.get(fileKey(target.path, target.agentId))).rejects.toThrow();
    expect(model.getValue()).toBe("my text");
    expect(doc.state.phase).toBe(code === "stale_file" ? "stale" : "failed");
    doc.dispose();
    const reopened = new FileDocument(target, modelDouble(), files, vi.fn(), undefined, true);
    await reopened.load();
    expect(reopened.model.getValue()).toBe("my text");
    expect(files.drafts.get(fileKey(target.path, target.agentId))?.baseHash).toBe("base-hash");
    reopened.dispose();
  },
);
