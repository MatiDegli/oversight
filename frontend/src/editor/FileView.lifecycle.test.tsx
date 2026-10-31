import type { FileContent, FileSaved } from "@contracts";
import type { editor } from "monaco-editor";
import { describe, expect, it, vi } from "vitest";

import { CommandError } from "../transport";
import { showFile } from "./FileView";
import { modelDouble } from "./fileModel.testSupport";
import { content, deferred, saved, setup, target } from "./FileView.testSupport";

const monacoDouble = vi.hoisted(() => ({
  createModel: vi.fn(),
  EndOfLineSequence: { LF: 0, CRLF: 1 },
}));
vi.mock("./monaco", () => ({ monaco: { editor: monacoDouble } }));

describe("file view document with a doubled Monaco model", () => {
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
