import type { FileContent, FileSaved } from "@contracts";
import { vi } from "vitest";

import { createFileSession } from "../app";
import { FileDocument } from "./fileDocument";
import { modelDouble } from "./fileModel.testSupport";
import type { FileLocation } from "./fileDocument";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

const target: FileLocation = { path: "shop/cart.py", agentId: null, startLine: 5, endLine: 9 };
const content = (text = "original", hash = "h0", path = target.path): FileContent => ({
  path,
  content: text,
  hash,
  agent_id: null,
});

function setup() {
  const commands = {
    getFile: vi
      .fn<(path: string, agent?: string) => Promise<FileContent>>()
      .mockImplementation((path) => Promise.resolve(content("original", "h0", path))),
    saveFile: vi
      .fn<(body: { path: string; content: string; base_hash: string }) => Promise<FileSaved>>()
      .mockResolvedValue({ path: target.path, hash: "h1" }),
  };
  const files = createFileSession(commands);
  const changed = vi.fn();
  const open = (location = target) => {
    const model = modelDouble();
    return { model, doc: new FileDocument(location, model, files, changed) };
  };
  return { commands, files, changed, open };
}

async function saved(files: ReturnType<typeof createFileSession>) {
  await files.inFlightSaves.get(target.path);
}

export { content, deferred, saved, setup, target };
