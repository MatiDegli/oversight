import { existsSync, readFileSync } from "node:fs";

import { afterEach, expect, it, vi } from "vitest";

vi.mock("@monaco-editor/react", () => ({ loader: { config: vi.fn() } }));
vi.mock("monaco-editor", () => ({ editor: {} }));
vi.mock("monaco-editor/editor/editor.worker.js?worker", () => ({
  default: class LocalEditorWorker {
    readonly name = "local-worker";
  },
}));

afterEach(() => vi.unstubAllGlobals());

it("ships DOMPurify 3.4.17 in Monaco's vendored sanitizer", () => {
  const source = readFileSync(
    new URL(import.meta.resolve("monaco-editor/base/browser/dompurify/dompurify.js")),
    "utf8",
  );
  expect(source, "Restore the Monaco DOMPurify patch before updating Monaco").toContain(
    "@license DOMPurify 3.4.17 |",
  );
  expect(source).toMatch(/DOMPurify\.version = ["']3\.4\.17["']/);
});

it("resolves the configured worker through the installed package exports", () => {
  const source = readFileSync(new URL("./monaco.ts", import.meta.url), "utf8");
  const worker = /from "([^"]+)\?worker"/.exec(source)?.[1];
  if (!worker) throw new Error("Missing bundled worker import");
  expect(existsSync(new URL(import.meta.resolve(worker)))).toBe(true);
});

it("configures the React loader with the local Monaco and a bundled worker", async () => {
  const scope: { MonacoEnvironment?: { getWorker: () => unknown } } = {};
  vi.stubGlobal("self", scope);
  const { monaco } = await import("./monaco");
  const { loader } = await import("@monaco-editor/react");
  const { default: EditorWorker } = await import("monaco-editor/editor/editor.worker.js?worker");
  expect(loader.config).toHaveBeenCalledWith({ monaco });
  expect(scope.MonacoEnvironment?.getWorker()).toBeInstanceOf(EditorWorker);
});
