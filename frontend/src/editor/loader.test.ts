import { expect, it, vi } from "vitest";

const gate = vi.hoisted(() => {
  let draw!: () => void;
  const promise = new Promise<void>((resolve) => {
    draw = resolve;
  });
  return { promise, draw, evaluated: vi.fn() };
});
vi.mock("../graph/firstDraw", () => ({ whenFirstGraphDrawn: () => gate.promise }));
vi.mock("./implementation", () => {
  gate.evaluated();
  return { FileView: "file" };
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
