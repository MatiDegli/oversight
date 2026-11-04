import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { stdout } from "node:process";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

import { chromium } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";

import { main, writeFirstViewMetrics, writeSceneMetrics } from "./capture.ts";
import { planJobs } from "./plan.ts";
import { scenes, themes } from "./scenes.ts";

const graphMetrics = { minLabelPx: 12, edgeShape: "bezier", nodes: 42 };

function fakePage(
  scenario: {
    unmasked: string | null;
    plain: string | null;
    webgl1?: boolean;
    noContext?: boolean;
  },
  withGraph = true,
) {
  let released = 0;
  const gl = {
    RENDERER: 1,
    getExtension(name: string) {
      if (name === "WEBGL_lose_context") return { loseContext: () => released++ };
      return name === "WEBGL_debug_renderer_info" && scenario.unmasked !== null
        ? { UNMASKED_RENDERER_WEBGL: 2 }
        : null;
    },
    getParameter(parameter: number) {
      return parameter === 2 ? scenario.unmasked : scenario.plain;
    },
  };
  const document = {
    fonts: { ready: Promise.resolve() },
    documentElement: { dataset: {} },
    querySelector: () => (withGraph ? { oversightGraph: { metrics: () => graphMetrics } } : null),
    createElement: () => ({
      getContext: (kind: string) =>
        scenario.noContext || (scenario.webgl1 && kind === "webgl2") ? null : gl,
    }),
  };
  // Run the browser callback in its own realm, as Playwright does, without requiring Chromium.
  const page = {
    evaluate: async (callback: (argument: unknown) => unknown, argument: unknown) => {
      const result: unknown = runInNewContext(`(${callback.toString()})(argument)`, {
        document,
        argument,
        requestAnimationFrame: (done: () => void) => {
          done();
        },
      });
      return await Promise.resolve(result);
    },
  } as unknown as Page;
  return { page, released: () => released };
}

for (const scenario of [
  { unmasked: "Unmasked GPU", plain: "WebGL GPU", expected: "Unmasked GPU" },
  { unmasked: null, plain: "WebGL GPU", expected: "WebGL GPU" },
  { unmasked: "", plain: "WebGL GPU", expected: "WebGL GPU" },
  { unmasked: null, plain: null, expected: "unavailable" },
  { unmasked: null, plain: "", expected: "unavailable" },
  { unmasked: null, plain: "WebGL 1 GPU", webgl1: true, expected: "WebGL 1 GPU" },
  { unmasked: null, plain: null, noContext: true, expected: "unavailable" },
]) {
  void test(`scene metrics write ${JSON.stringify(scenario)} without changing graph metrics`, async () => {
    const directory = await mkdtemp(join(tmpdir(), "shot-metrics-"));
    const { page, released } = fakePage(scenario);
    try {
      const path = join(directory, "watch-dark.json");
      await writeSceneMetrics(page, path);
      assert.deepEqual(JSON.parse(await readFile(path, "utf8")), {
        ...graphMetrics,
        webglRenderer: scenario.expected,
      });
      assert.equal(
        await readFile(path, "utf8"),
        JSON.stringify({ ...graphMetrics, webglRenderer: scenario.expected }, null, 2) + "\n",
      );
      assert.equal(released(), scenario.noContext ? 0 : 1);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });

  void test(`first-view metrics write ${JSON.stringify(scenario)} without the graph`, async () => {
    const directory = await mkdtemp(join(tmpdir(), "shot-first-view-metrics-"));
    const { page, released } = fakePage(scenario, false);
    try {
      const path = join(directory, "first-1280-dark.json");
      await writeFirstViewMetrics(page, path);
      assert.deepEqual(JSON.parse(await readFile(path, "utf8")), {
        firstView: true,
        webglRenderer: scenario.expected,
      });
      assert.equal(
        await readFile(path, "utf8"),
        JSON.stringify({ firstView: true, webglRenderer: scenario.expected }, null, 2) + "\n",
      );
      assert.equal(released(), scenario.noContext ? 0 : 1);
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
}

void test("scene metrics still require the graph", async () => {
  const directory = await mkdtemp(join(tmpdir(), "shot-missing-graph-"));
  const { page } = fakePage({ unmasked: null, plain: "WebGL GPU" }, false);
  try {
    await assert.rejects(writeSceneMetrics(page, join(directory, "watch-dark.json")), {
      message: "Graph renderer missing",
    });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

void test("default PNGs and their JSON evidence fit the stager's 64-file cap", () => {
  const enabled = planJobs(scenes, themes).filter((job) => job.skip === null);
  assert.ok(enabled.length > 0);
  assert.ok(enabled.length <= 32, `${String(enabled.length)} PNGs exceed the 32-shot budget`);
});

function fakeCapturePage(firstView: boolean) {
  const { page, released } = fakePage({ unmasked: null, plain: "WebGL GPU" }, !firstView);
  const locator = {
    first: () => locator,
    waitFor: async () => {},
    isVisible: () => Promise.resolve(true),
    getAttribute: () => Promise.resolve("true"),
    getByRole: () => locator,
  };
  Object.assign(page, {
    locator: () => locator,
    getByRole: (_role: string, options: { name: string }) => {
      if (firstView) assert.equal(options.name, "Chat", "first view must stay in Chat");
      else assert.equal(options.name, "Workspace view");
      return locator;
    },
    getByLabel: () => ({ count: () => Promise.resolve(firstView ? 0 : 1) }),
    on: () => {},
    route: async () => {},
    goto: async () => {},
    waitForFunction: async () => {},
    // Stub bytes are only for testing the dispatcher and evidence files, not real UI captures.
    screenshot: async ({ path }: { path: string }) => writeFile(path, "stub screenshot"),
  });
  return { page, released };
}

void test("main writes same-stem evidence for first-view and graph captures", async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "shot-dispatch-"));
  const captureScenes = scenes.filter((scene) => scene.firstView || scene.name === "watch");
  const jobs = planJobs(captureScenes, themes).filter((job) => job.skip === null);
  const output: string[] = [];
  const pages: ReturnType<typeof fakeCapturePage>[] = [];
  const closeBrowser = t.mock.fn(async () => {});
  const closeContext = t.mock.fn(async () => {});
  const browser = {
    newContext: ({ viewport }: { viewport: { width: number } }) => {
      const page = fakeCapturePage(viewport.width !== 1920);
      pages.push(page);
      return Promise.resolve({
        setDefaultTimeout: () => {},
        newPage: () => Promise.resolve(page.page),
        close: closeContext,
      });
    },
    close: closeBrowser,
  } as unknown as Browser;
  t.mock.method(chromium, "launch", () => Promise.resolve(browser));
  t.mock.method(stdout, "write", (chunk: string) => {
    output.push(chunk);
    return true;
  });
  try {
    const log = join(directory, "source.events");
    await writeFile(log, '{"seq":1,"type":"run.started","payload":{"slots":[{},{}]}}\n');
    assert.equal(
      await main(["--url", "http://127.0.0.1/", "--log", log, "--out", directory], captureScenes),
      0,
      output.join(""),
    );
    const pngs = (await readdir(directory)).filter((name) => name.endsWith(".png"));
    assert.deepEqual(pngs.sort(), jobs.map((job) => job.file).sort());
    assert.equal(pngs.filter((name) => name.startsWith("first-")).length, 4);
    for (const png of pngs) {
      const evidence = join(directory, png.replace(/\.png$/, ".json"));
      assert.ok((await stat(evidence)).isFile());
      const firstView = png.startsWith("first-");
      assert.equal(
        await readFile(evidence, "utf8"),
        JSON.stringify(
          { ...(firstView ? { firstView: true } : graphMetrics), webglRenderer: "WebGL GPU" },
          null,
          2,
        ) + "\n",
      );
      assert.equal(
        output.includes(`shots: ${png} label minimum 12.00 CSS px, bezier\n`),
        !firstView,
      );
    }
    assert.ok(pages.every(({ released }) => released() === 1));
    assert.equal(closeContext.mock.callCount(), jobs.length);
    assert.equal(closeBrowser.mock.callCount(), 1);
  } finally {
    t.mock.restoreAll();
    await rm(directory, { recursive: true, force: true });
  }
});
