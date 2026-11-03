import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

import type { Page } from "@playwright/test";

import { writeFirstViewMetrics, writeSceneMetrics } from "./capture.ts";
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
    querySelector: () => (withGraph ? { oversightGraph: { metrics: () => graphMetrics } } : null),
    createElement: () => ({
      getContext: (kind: string) =>
        scenario.noContext || (scenario.webgl1 && kind === "webgl2") ? null : gl,
    }),
  };
  // Run the browser callback in its own realm, as Playwright does, without requiring Chromium.
  const page = {
    evaluate: async (callback: () => unknown) => {
      const result: unknown = runInNewContext(`(${callback.toString()})()`, { document });
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
