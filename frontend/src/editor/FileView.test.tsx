import type { FileContent, FileSaved } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { CommandError } from "../transport";
import { FileView } from "./FileView";
import { FileDocument, staleMessage } from "./fileDocument";
import { modelDouble } from "./fileModel.testSupport";
import { content, deferred, saved, setup, target } from "./FileView.testSupport";

const monacoDouble = vi.hoisted(() => ({
  createModel: vi.fn(),
  EndOfLineSequence: { LF: 0, CRLF: 1 },
}));
vi.mock("./monaco", () => ({ monaco: { editor: monacoDouble } }));

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
});
