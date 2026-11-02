import { beforeEach, expect, it, vi } from "vitest";

const gate = vi.hoisted(() => ({
  promise: Promise.resolve(),
  draw: () => {},
  evaluated: vi.fn(),
}));
vi.mock("../graph/firstDraw", () => ({ whenFirstGraphDrawn: () => gate.promise }));
beforeEach(() => {
  vi.resetModules();
  gate.evaluated.mockClear();
  vi.doMock("./implementation", () => {
    gate.evaluated();
    return { FileView: "file" };
  });
  gate.promise = new Promise<void>((resolve) => {
    gate.draw = resolve;
  });
});

it("shares one evaluated module across prefetch and all opens, after the first graph", async () => {
  const { loadEditor, loadedEditor } = await import("./loader");
  const prefetched = loadEditor();
  expect(loadEditor()).toBe(prefetched);
  await Promise.resolve();
  expect(gate.evaluated).not.toHaveBeenCalled();
  expect(loadedEditor()).toBeUndefined();
  gate.draw();
  expect(await prefetched).toHaveProperty("FileView", "file");
  expect(loadedEditor()).toBe(await prefetched);
  expect(loadEditor()).toBe(prefetched);
  expect(gate.evaluated).toHaveBeenCalledOnce();
});
it("releases a pending explicit load without a graph draw", async () => {
  const { loadEditor, allowEditorWithoutGraph } = await import("./loader");
  const opened = loadEditor();
  await Promise.resolve();
  expect(gate.evaluated).not.toHaveBeenCalled();
  allowEditorWithoutGraph();
  expect(await opened).toHaveProperty("FileView", "file");
});
it("grants eligibility without importing until a subsequent request", async () => {
  const { allowEditorWithoutGraph, editorReady, loadedEditor, loadEditor } =
    await import("./loader");
  allowEditorWithoutGraph();
  await editorReady;
  expect(loadedEditor()).toBeUndefined();
  expect(gate.evaluated).not.toHaveBeenCalled();
  await loadEditor();
  expect(gate.evaluated).toHaveBeenCalledOnce();
});
it("remembers release before subscription and shares evaluation across repeated releases", async () => {
  const { allowEditorWithoutGraph, editorReady, loadEditor, loadedEditor } =
    await import("./loader");
  allowEditorWithoutGraph();
  allowEditorWithoutGraph();
  await editorReady;
  const first = loadEditor();
  allowEditorWithoutGraph();
  gate.draw();
  expect(loadEditor()).toBe(first);
  expect(await first).toBe(loadedEditor());
  expect(gate.evaluated).toHaveBeenCalledOnce();
});
