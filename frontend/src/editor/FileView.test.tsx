import type { FileContent, FileSaved } from "@contracts";
import type { editor } from "monaco-editor";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { createFileSession } from "../app";
import { CommandError } from "../transport";
import { FileView, showFile } from "./FileView";
import { FileDocument, staleMessage } from "./fileDocument";
import { modelDouble } from "./fileModel.testSupport";
import type { FileLocation } from "./fileDocument";

const monacoDouble = vi.hoisted(() => ({
  createModel: vi.fn(),
  EndOfLineSequence: { LF: 0, CRLF: 1 },
}));
vi.mock("./monaco", () => ({ monaco: { editor: monacoDouble } }));

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

describe("file view document with a doubled Monaco model", () => {
  it.each([
    ["BOM", "\uFEFFfirst\nsecond\n", 2, 1, "edited ", "\uFEFFfirst\nedited second\n"],
    [
      "mixed EOL",
      "first\r\nsecond\nthird\r\n",
      2,
      1,
      "edited ",
      "first\r\nedited second\nthird\r\n",
    ],
    ["empty file with CRLF browser default", "", 1, 1, "\n", "\n"],
  ])(
    "sends the expected UTF-8 bytes after editing a %s",
    async (_, raw, line, column, edit, expected) => {
      const { commands, files } = setup();
      commands.getFile.mockResolvedValue(content(raw));
      const model = modelDouble("\r\n");
      const doc = new FileDocument(target, model, files, vi.fn());
      await doc.load();
      model.applyEdits([
        {
          range: {
            startLineNumber: line,
            startColumn: column,
            endLineNumber: line,
            endColumn: column,
          },
          text: edit,
        },
      ]);
      expect(doc.state).toMatchObject({ dirty: true, canSave: true });
      doc.save();
      await saved(files);
      const sent = commands.saveFile.mock.calls[0]?.[0].content;
      expect(new TextEncoder().encode(sent)).toEqual(new TextEncoder().encode(expected));
      expect(doc.state).toMatchObject({ phase: "clean", dirty: false });
      expect(files.drafts.size).toBe(0);
      doc.dispose();
    },
  );

  it("keeps BOM and mixed EOL in a draft across closing, reopening, inserting and deleting lines", async () => {
    const { commands, files } = setup();
    commands.getFile.mockResolvedValue(content("\uFEFFfirst\r\nsecond\nthird\r\nfourth"));
    const first = new FileDocument(target, modelDouble("\r\n"), files, vi.fn());
    await first.load();
    first.model.applyEdits([
      {
        range: { startLineNumber: 2, startColumn: 1, endLineNumber: 2, endColumn: 1 },
        text: "inserted\n",
      },
    ]);
    first.dispose();
    const returned = new FileDocument(target, modelDouble("\r\n"), files, vi.fn());
    await returned.load();
    returned.model.applyEdits([
      {
        range: { startLineNumber: 4, startColumn: 1, endLineNumber: 5, endColumn: 1 },
        text: "",
      },
    ]);
    returned.save();
    await saved(files);
    expect(commands.saveFile).toHaveBeenCalledExactlyOnceWith({
      path: target.path,
      content: "\uFEFFfirst\r\ninserted\r\nsecond\nfourth",
      base_hash: "h0",
    });
    returned.dispose();
  });

  it.each([
    ["BOM", "\uFEFFfirst\nsecond\n", "first\nsecond\n"],
    ["mixed EOL", "first\r\nsecond\nthird\r\n", "first\r\nsecond\r\nthird\r\n"],
  ])(
    "an untouched %s file stays clean through load, refresh, Reload and Discard",
    async (_, raw, normalized) => {
      const { open, commands, files } = setup();
      commands.getFile.mockResolvedValue(content(raw));
      const { doc, model } = open();
      for (const reload of [false, false, true]) {
        await doc.load(reload);
        expect(model.getValue()).toBe(normalized);
        expect(doc.state).toMatchObject({ phase: "clean", dirty: false, canSave: false });
        expect(files.drafts.size).toBe(0);
        doc.save();
      }
      expect(commands.saveFile).not.toHaveBeenCalled();
      model.setValue(normalized + "edited");
      expect(doc.state.dirty).toBe(true);
      doc.discard();
      expect(doc.state).toMatchObject({ phase: "clean", dirty: false, canSave: false });
      expect(files.drafts.size).toBe(0);
      doc.dispose();
      const reopened = open();
      await reopened.doc.load();
      expect(reopened.doc.state.dirty).toBe(false);
    },
  );

  it.each([
    [new TypeError("Failed to fetch"), "did not reach the server"],
    [new CommandError(502, undefined, "HTTP 502 Bad Gateway"), "the server hit an internal error"],
    [
      new CommandError(404, undefined, "HTTP 404 Not Found"),
      "the server could not complete the request",
    ],
  ])("describes load and save failures in plain words: %s", async (error, explanation) => {
    const { open, commands, files } = setup();
    commands.getFile.mockRejectedValueOnce(error);
    const { doc, model } = open();
    await doc.load();
    expect(doc.state.message).toContain(explanation);
    expect(doc.state.message).toMatch(/try again/i);
    expect(doc.state.message).not.toContain(error.message);
    await doc.load(true);
    model.setValue("draft");
    commands.saveFile.mockRejectedValueOnce(error);
    doc.save();
    await saved(files).catch(() => undefined);
    expect(doc.state.message).toContain(explanation);
    expect(doc.state.message).not.toContain(error.message);
    expect(doc.state.dirty).toBe(true);
    expect(files.drafts.get(target.path)?.content).toBe("draft");
  });

  it("loads, becomes dirty, saves with its base hash, adopts the returned hash and becomes clean", async () => {
    const { open, commands, files } = setup();
    const { doc, model } = open();
    expect(doc.state.phase).toBe("loading");
    await doc.load();
    expect(commands.getFile).toHaveBeenCalledWith(target.path, undefined);
    expect(model.getValue()).toBe("original");
    expect(doc.state).toMatchObject({ phase: "clean", dirty: false });
    model.setValue("v1");
    expect(doc.state).toMatchObject({ phase: "dirty", dirty: true });
    expect(files.drafts.get(target.path)).toEqual({ content: "v1", baseHash: "h0" });
    doc.save();
    expect(doc.state.phase).toBe("saving");
    doc.save();
    await saved(files);
    expect(commands.saveFile).toHaveBeenCalledExactlyOnceWith({
      path: target.path,
      content: "v1",
      base_hash: "h0",
    });
    expect(doc.state.phase).toBe("clean");
    expect(files.drafts.has(target.path)).toBe(false);
    model.setValue("v2");
    doc.save();
    await saved(files);
    expect(commands.saveFile).toHaveBeenLastCalledWith({
      path: target.path,
      content: "v2",
      base_hash: "h1",
    });
  });

  it("keeps edits typed during a save dirty against the saved text", async () => {
    const { open, commands, files } = setup();
    const held = deferred<FileSaved>();
    commands.saveFile.mockReturnValueOnce(held.promise);
    const { doc, model } = open();
    await doc.load();
    model.setValue("v1");
    doc.save();
    model.setValue("v2");
    held.resolve({ path: target.path, hash: "h1" });
    await saved(files);
    expect(model.getValue()).toBe("v2");
    expect(doc.state.phase).toBe("dirty");
    expect(files.drafts.get(target.path)).toEqual({ content: "v2", baseHash: "h1" });
  });

  it("stale_file keeps edits, refuses overwrite, and Reload explicitly replaces them", async () => {
    const { open, commands, files } = setup();
    commands.saveFile.mockRejectedValueOnce(new CommandError(409, "stale_file", "Moved"));
    const { doc, model } = open();
    await doc.load();
    model.setValue("draft");
    doc.save();
    await saved(files).catch(() => undefined);
    expect(doc.state).toMatchObject({ phase: "stale", message: staleMessage, dirty: true });
    expect(model.getValue()).toBe("draft");
    doc.save();
    expect(commands.saveFile).toHaveBeenCalledOnce();
    commands.getFile.mockResolvedValueOnce(content("server", "h2"));
    await doc.load(true);
    expect(model.getValue()).toBe("server");
    expect(doc.state.phase).toBe("clean");
    expect(files.drafts.has(target.path)).toBe(false);
    model.setValue("after reload");
    doc.save();
    await saved(files);
    expect(commands.saveFile).toHaveBeenLastCalledWith({
      path: target.path,
      content: "after reload",
      base_hash: "h2",
    });
  });

  it("other save errors keep the message and draft, and allow retry", async () => {
    const { open, commands, files } = setup();
    commands.saveFile.mockRejectedValueOnce(
      new CommandError(409, "wrong_state", "Main is merging. Try again."),
    );
    const { doc, model } = open();
    await doc.load();
    model.setValue("draft");
    doc.save();
    await saved(files).catch(() => undefined);
    expect(doc.state).toMatchObject({
      phase: "failed",
      dirty: true,
      message: "Main is merging. Try again.",
    });
    expect(model.getValue()).toBe("draft");
    expect(files.drafts.get(target.path)?.content).toBe("draft");
    doc.save();
    await saved(files);
    expect(doc.state.phase).toBe("clean");
  });

  it("reports a failed load and lets Reload recover", async () => {
    const { open, commands } = setup();
    commands.getFile.mockRejectedValueOnce(
      new CommandError(404, "not_found", "File no longer exists."),
    );
    const { doc, model } = open();
    await doc.load();
    expect(doc.state).toMatchObject({
      phase: "failed",
      loaded: false,
      message: "File no longer exists.",
    });
    await doc.load(true);
    expect(doc.state.phase).toBe("clean");
    expect(model.getValue()).toBe("original");
  });

  it("a failed clean refresh retains the file and offers a load retry", async () => {
    const { open, commands } = setup();
    const { doc, model } = open();
    await doc.load();
    commands.getFile.mockRejectedValueOnce(
      new CommandError(500, "internal", "The file could not be read. Try again."),
    );
    await doc.load();
    expect(doc.state).toMatchObject({
      phase: "failed",
      loaded: true,
      dirty: false,
      retryLoad: true,
      canSave: false,
    });
    expect(model.getValue()).toBe("original");
    await doc.load(true);
    expect(doc.state).toMatchObject({ phase: "clean", retryLoad: false });
  });

  it("reads agent targets without saving or keeping main drafts", async () => {
    const { open, commands, files } = setup();
    const { doc, model } = open({ ...target, agentId: "agent-1" });
    await doc.load();
    expect(commands.getFile).toHaveBeenCalledWith(target.path, "agent-1");
    model.setValue("cannot save");
    doc.save();
    expect(commands.saveFile).not.toHaveBeenCalled();
    expect(files.drafts.size).toBe(0);
    const markup = renderToStaticMarkup(
      <FileView
        target={{ ...target, agentId: "agent-1" }}
        files={files}
        commit="c0"
        onClose={vi.fn()}
      />,
    );
    expect(markup).toContain("Read-only");
    expect(markup).not.toContain(">Save</button>");
  });

  it("a draft survives a switch, a review or Escape and returns with its original base hash", async () => {
    const { open, files } = setup();
    const first = open();
    await first.doc.load();
    first.model.setValue("draft");
    first.doc.dispose();
    const other = open({ ...target, path: "shop/orders.py" });
    await other.doc.load();
    other.doc.dispose();
    const returned = open();
    await returned.doc.load();
    expect(returned.model.getValue()).toBe("draft");
    expect(returned.doc.state.phase).toBe("dirty");
    expect(files.drafts.get(target.path)).toEqual({ content: "draft", baseHash: "h0" });
  });

  it("a draft over a moved file shows the stale banner immediately; Discard deletes it", async () => {
    const { open, files, commands } = setup();
    files.drafts.set(target.path, { content: "draft", baseHash: "old" });
    commands.getFile.mockResolvedValueOnce(content("moved", "new"));
    const { doc, model } = open();
    await doc.load();
    expect(doc.state).toMatchObject({ phase: "stale", message: staleMessage });
    expect(model.getValue()).toBe("draft");
    doc.discard();
    expect(model.getValue()).toBe("moved");
    expect(doc.state.phase).toBe("clean");
    expect(files.drafts.has(target.path)).toBe(false);
  });

  it("a current refresh answering after typing keeps text, notes the server hash and shows stale", async () => {
    const { open, commands, files } = setup();
    const { doc, model } = open();
    await doc.load();
    const refresh = deferred<FileContent>();
    commands.getFile.mockReturnValueOnce(refresh.promise);
    const loading = doc.load();
    model.setValue("typing");
    refresh.resolve(content("server", "h2"));
    await loading;
    expect(model.getValue()).toBe("typing");
    expect(files.drafts.get(target.path)).toEqual({ content: "typing", baseHash: "h0" });
    expect(doc.state.phase).toBe("stale");
    doc.discard();
    expect(model.getValue()).toBe("server");
  });

  it("a clean file refetches only for a new commit; dirty files are not refreshed", async () => {
    const { open, commands } = setup();
    const { doc, model } = open();
    doc.observeCommit("c0");
    await doc.load();
    doc.observeCommit("c0");
    expect(commands.getFile).toHaveBeenCalledTimes(1);
    commands.getFile.mockResolvedValueOnce(content("new commit", "h2"));
    doc.observeCommit("c1");
    await vi.waitFor(() => {
      expect(model.getValue()).toBe("new commit");
    });
    expect(model.getValue()).toBe("new commit");
    model.setValue("draft");
    doc.observeCommit("c2");
    expect(commands.getFile).toHaveBeenCalledTimes(2);
  });

  it("F2: save v1, type v2, open review before 200, then return and save v2 with the new hash", async () => {
    const { open, commands, files } = setup();
    const held = deferred<FileSaved>();
    commands.saveFile.mockReturnValueOnce(held.promise);
    const first = open();
    await first.doc.load();
    first.model.setValue("v1");
    first.doc.save();
    first.model.setValue("v2");
    first.doc.dispose();
    const pending = files.inFlightSaves.get(target.path);
    held.resolve({ path: target.path, hash: "h1" });
    await pending;
    expect(files.drafts.get(target.path)).toEqual({ content: "v2", baseHash: "h1" });
    commands.getFile.mockResolvedValueOnce(content("v1", "h1"));
    const returned = open();
    await returned.doc.load();
    expect(returned.model.getValue()).toBe("v2");
    expect(returned.doc.state.phase).toBe("dirty");
    returned.doc.save();
    await saved(files);
    expect(commands.saveFile).toHaveBeenLastCalledWith({
      path: target.path,
      content: "v2",
      base_hash: "h1",
    });
    expect(returned.doc.state.phase).toBe("clean");
  });

  it("F2: reopening during PUT shows saving, sends no duplicate, and adopts the same settlement", async () => {
    const { open, commands, files } = setup();
    const held = deferred<FileSaved>();
    commands.saveFile.mockReturnValueOnce(held.promise);
    const first = open();
    await first.doc.load();
    first.model.setValue("v1");
    first.doc.save();
    first.doc.dispose();
    const returned = open();
    await returned.doc.load();
    expect(returned.doc.state.phase).toBe("saving");
    returned.doc.save();
    const pending = files.inFlightSaves.get(target.path);
    held.resolve({ path: target.path, hash: "h1" });
    await pending;
    expect(commands.saveFile).toHaveBeenCalledOnce();
    expect(returned.doc.state.phase).toBe("clean");
    expect(returned.model.getValue()).toBe("v1");
    expect(files.drafts.has(target.path)).toBe(false);
  });

  it("F2: a successful save deletes an unmounted matching draft; failure leaves it untouched", async () => {
    const { open, commands, files } = setup();
    const held = deferred<FileSaved>();
    commands.saveFile.mockReturnValueOnce(held.promise);
    const first = open();
    await first.doc.load();
    first.model.setValue("v1");
    first.doc.save();
    first.doc.dispose();
    const pending = files.inFlightSaves.get(target.path);
    held.resolve({ path: target.path, hash: "h1" });
    await pending;
    expect(files.drafts.has(target.path)).toBe(false);
    commands.getFile.mockResolvedValueOnce(content("v1", "h1"));
    const next = open();
    await next.doc.load();
    next.model.setValue("v2");
    const refusal = deferred<FileSaved>();
    commands.saveFile.mockReturnValueOnce(refusal.promise);
    next.doc.save();
    next.doc.dispose();
    const failed = files.inFlightSaves.get(target.path);
    refusal.reject(new CommandError(409, "stale_file", "Moved"));
    await failed?.catch(() => undefined);
    expect(files.drafts.get(target.path)).toEqual({ content: "v2", baseHash: "h1" });
  });

  it("F3: A → B → A discards the older A response entirely even with repeating model versions", async () => {
    const { open, commands } = setup();
    const oldGet = deferred<FileContent>();
    commands.getFile.mockReturnValueOnce(oldGet.promise);
    const oldA = open();
    const oldLoad = oldA.doc.load();
    oldA.doc.dispose();
    const b = open({ ...target, path: "shop/orders.py" });
    await b.doc.load();
    b.doc.dispose();
    commands.getFile.mockResolvedValueOnce(content("new A", "new"));
    const newA = open();
    expect(newA.model.getVersionId()).toBe(oldA.model.getVersionId());
    await newA.doc.load();
    oldGet.resolve(content("old A", "old"));
    await oldLoad;
    expect(oldA.model.getValue()).toBe("");
    expect(newA.model.getValue()).toBe("new A");
    expect(newA.doc.state).toMatchObject({ phase: "clean", message: null });
    newA.model.setValue("edit");
    newA.doc.save();
    await Promise.resolve();
    expect(commands.saveFile).toHaveBeenLastCalledWith({
      path: target.path,
      content: "edit",
      base_hash: "new",
    });
  });

  it("F3: a GET of A after a switch leaves B text, hash and banner untouched", async () => {
    const { open, commands, files } = setup();
    const held = deferred<FileContent>();
    commands.getFile.mockReturnValueOnce(held.promise);
    const a = open();
    const aLoad = a.doc.load();
    a.doc.dispose();
    const path = "shop/orders.py";
    commands.getFile.mockResolvedValueOnce(content("B", "b-hash", path));
    const b = open({ ...target, path });
    await b.doc.load();
    held.resolve(content("A", "a-hash"));
    await aLoad;
    expect(a.model.getValue()).toBe("");
    expect(b.model.getValue()).toBe("B");
    expect(b.doc.state.message).toBeNull();
    b.model.setValue("B edit");
    expect(files.drafts.get(path)?.baseHash).toBe("b-hash");
  });

  it("F3: only the latest issued GET may update a model or its stored server content", async () => {
    const { open, commands } = setup();
    const { doc, model } = open();
    await doc.load();
    const older = deferred<FileContent>();
    commands.getFile.mockReturnValueOnce(older.promise);
    const oldLoad = doc.load();
    commands.getFile.mockResolvedValueOnce(content("new", "h2"));
    await doc.load();
    older.resolve(content("old", "h-old"));
    await oldLoad;
    expect(model.getValue()).toBe("new");
    expect(doc.state.message).toBeNull();
    doc.discard();
    expect(model.getValue()).toBe("new");
  });
});

it("attaches a new model before disposing the old, chooses Python or plaintext, and does not focus", () => {
  const { files } = setup();
  const firstModel = modelDouble();
  const secondModel = modelDouble();
  const order: string[] = [];
  monacoDouble.createModel.mockReturnValueOnce(firstModel).mockReturnValueOnce(secondModel);
  const code = {
    saveViewState: vi.fn(() => null),
    restoreViewState: vi.fn(),
    setModel: vi.fn(() => {
      order.push("attach");
    }),
  };
  const first = showFile(code, null, target, files, vi.fn());
  vi.spyOn(firstModel, "dispose").mockImplementation(() => {
    order.push("dispose");
  });
  const next = showFile(code, first, { ...target, path: "notes.txt" }, files, vi.fn());
  expect(monacoDouble.createModel.mock.calls).toEqual([
    ["", "python"],
    ["", "plaintext"],
  ]);
  expect(order).toEqual(["attach", "attach", "dispose"]);
  expect(next.model).toBe(secondModel);
  expect(vi.spyOn(firstModel, "updateOptions")).toHaveBeenCalledWith({
    bracketColorizationOptions: { enabled: false, independentColorPoolPerBracketType: false },
  });
  firstModel.setValue("disposed listener");
  expect(files.drafts.size).toBe(0);
});

it("restores the span decoration and editor view after Reload, refresh and Discard", async () => {
  const { commands, files } = setup();
  const model = modelDouble();
  monacoDouble.createModel.mockReturnValueOnce(model);
  const view = { scrollTop: 126, scrollLeft: 20 } as unknown as editor.ICodeEditorViewState;
  let decorated = false;
  const setValue = model.setValue.bind(model);
  vi.spyOn(model, "setValue").mockImplementation((text) => {
    decorated = false; // Monaco clears all decorations on setValue.
    setValue(text);
  });
  const decorate = vi.fn(() => {
    decorated = true;
  });
  const code = {
    setModel: vi.fn(),
    saveViewState: vi.fn(() => view),
    restoreViewState: vi.fn((restored) => {
      expect(restored).toBe(view);
      expect(decorated).toBe(true);
    }),
  };
  const doc = showFile(code, null, target, files, vi.fn(), decorate);
  await doc.load();
  for (const reload of [true, false]) {
    commands.getFile.mockResolvedValueOnce(content(`replacement ${String(reload)}`, "h2"));
    await doc.load(reload);
    expect(decorated).toBe(true);
  }
  model.setValue("draft");
  expect(decorated).toBe(false);
  doc.discard();
  expect(decorated).toBe(true);
  expect(decorate).toHaveBeenCalledTimes(4);
  expect(code.restoreViewState).toHaveBeenCalledTimes(4);
});
