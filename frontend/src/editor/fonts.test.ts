import { expect, it, vi } from "vitest";

import { loadEditorFonts } from "./fonts";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

it("waits for both requested fonts and the document font set before permitting creation", async () => {
  const mono = deferred<FontFace[]>();
  const text = deferred<FontFace[]>();
  const ready = deferred<FontFaceSet>();
  const load = vi.fn((font: string) => (font.includes(" Mono") ? mono.promise : text.promise));
  const permitCreation = vi.fn();
  const loading = loadEditorFonts({ load, ready: ready.promise }).then(permitCreation);
  ready.resolve({} as FontFaceSet);
  mono.resolve([]);
  await mono.promise;
  expect(permitCreation).not.toHaveBeenCalled();
  text.resolve([]);
  await loading;
  expect(permitCreation).toHaveBeenCalledOnce();
  expect(load.mock.calls).toEqual([
    ['15px "Atkinson Hyperlegible Mono"'],
    ['15px "Atkinson Hyperlegible Next"'],
  ]);
});

it("reports a font failure instead of permitting a wrongly measured editor", async () => {
  const permitCreation = vi.fn();
  const loading = loadEditorFonts({
    load: () => Promise.reject(new Error("Font unavailable")),
    ready: Promise.resolve({} as FontFaceSet),
  }).then(permitCreation);
  await expect(loading).rejects.toThrow("Font unavailable");
  expect(permitCreation).not.toHaveBeenCalled();
});
