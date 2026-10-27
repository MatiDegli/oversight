// `make shots` points Vite's cache under .shots/ with OVERSIGHT_VITE_CACHE_DIR; `make dev` sets
// nothing and keeps Vite's default. Resolving the config binds no port and starts no server.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { resolveConfig } from "vite";

import { fixtureCacheDir } from "../src/app/browserCache.support.ts";

const configFile = resolve(import.meta.dirname, "../vite.config.ts");

async function cacheDir(variable: string | undefined): Promise<string> {
  const before = process.env.OVERSIGHT_VITE_CACHE_DIR;
  if (variable === undefined) delete process.env.OVERSIGHT_VITE_CACHE_DIR;
  else process.env.OVERSIGHT_VITE_CACHE_DIR = variable;
  try {
    // The runner loader writes no temp file next to node_modules while the test resolves it.
    return (await resolveConfig({ configFile, configLoader: "runner" }, "serve")).cacheDir;
  } finally {
    if (before === undefined) delete process.env.OVERSIGHT_VITE_CACHE_DIR;
    else process.env.OVERSIGHT_VITE_CACHE_DIR = before;
  }
}

void test("OVERSIGHT_VITE_CACHE_DIR moves Vite's cache", async () => {
  assert.equal(await cacheDir("/somewhere/.shots/vite-cache"), "/somewhere/.shots/vite-cache");
});

void test("without it, the cache stays in node_modules/.vite, as make dev has it", async () => {
  assert.match(await cacheDir(undefined), /\/frontend\/node_modules\/\.vite$/);
  assert.match(await cacheDir(""), /\/frontend\/node_modules\/\.vite$/);
});

void test("concurrent app fixture families never write to the same Vite cache", () => {
  const families = [
    "app/camera",
    "app/dotField",
    "app/taskBox",
    "app/fileView",
    "app/functionTask",
    "app/review",
    "app/reviewControls",
    "editor/LiveDiff",
    "graph/folds",
  ];
  const caches = families.map((family) => {
    const fixture = new URL(`../src/${family}.browser.test.ts`, import.meta.url);
    let source = readFileSync(fixture, "utf8");
    const support = /from "(\.\/[^"]+\.browser\.support)"/.exec(source)?.[1];
    if (support) {
      source = readFileSync(new URL(`${support}.ts`, fixture), "utf8");
      assert.match(source, /cacheDir: fixtureCacheDir\(expect.getState\(\).testPath\)/);
      return fixtureCacheDir(fileURLToPath(fixture));
    }
    const cache = /cacheDir:\s*fileURLToPath\(new URL\("([^"]+)"/.exec(source)?.[1];
    assert.ok(cache, `${family} must give its Vite server an explicit cacheDir`);
    const directory = fileURLToPath(new URL(cache, fixture));
    assert.ok(directory.startsWith(resolve(import.meta.dirname, "../../.shots") + "/"));
    return directory;
  });
  assert.equal(new Set(caches).size, caches.length, "fixture caches must be distinct");
});

void test("the browser project caps concurrent fixture families at two workers", async () => {
  const config = await resolveConfig({ configFile, configLoader: "runner" }, "serve");
  const browser = config.test.projects.find(
    (project) =>
      typeof project === "object" && "test" in project && project.test?.name === "browser",
  );
  assert.ok(
    browser && typeof browser === "object" && "test" in browser,
    "the browser project must exist",
  );
  assert.equal(browser.test?.maxWorkers, 2);
});

void test("shared browser support requires the current test file for cache isolation", () => {
  assert.throws(() => fixtureCacheDir(undefined), /test file/);
  assert.ok(
    fixtureCacheDir("/repo/frontend/src/app/functionTask.browser.test.ts").startsWith(
      resolve(import.meta.dirname, "../../.shots") + "/",
    ),
  );
});
