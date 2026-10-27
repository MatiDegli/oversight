import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { build } from "vite";

import { measureStartupJs, startupFiles } from "../tools/startup-js.ts";

void test("startup traversal counts shared static chunks once and excludes dynamic imports", () => {
  const chunk = (file: string, imports: string[] = [], dynamicImports: string[] = []) => ({
    file,
    imports,
    dynamicImports,
  });
  assert.deepEqual(
    startupFiles({
      "index.html": chunk("entry.js", ["react", "graph"], ["editor"]),
      react: chunk("react.js", ["shared"]),
      graph: chunk("graph.js", ["shared"]),
      shared: chunk("shared.js", ["react"]),
      editor: chunk("monaco.js"),
    }),
    ["entry.js", "graph.js", "react.js", "shared.js"],
  );
  assert.throws(() => startupFiles({}), /Missing startup chunk/);
});

void test("the built replay transfers at most 400 KiB gzip of startup JavaScript", async () => {
  const directory = await mkdtemp(join(tmpdir(), "oversight-startup-"));
  try {
    await build({
      root: new URL("../", import.meta.url).pathname,
      configLoader: "runner",
      mode: "replay",
      logLevel: "error",
      build: { outDir: directory, emptyOutDir: true },
    });
    const measured = measureStartupJs(directory);
    console.log("Replay startup JS:", measured);
    assert.ok(
      measured.gzip <= 400 * 1024,
      `Startup JS exceeds 400 KiB gzip: ${String(measured.gzip)}`,
    );
    const workers = (await readdir(join(directory, "assets"))).filter((name) =>
      /\.worker-.*\.js$/.test(name),
    );
    assert.equal(workers.length, 1);
    assert.match(workers[0] ?? "", /^editor\.worker-/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
