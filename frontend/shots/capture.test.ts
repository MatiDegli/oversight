import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

import type { Page } from "@playwright/test";

import { writeSceneMetrics } from "./capture.ts";

const graphMetrics = { minLabelPx: 12, edgeShape: "bezier", nodes: 42 };

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
    const gl = {
      RENDERER: 1,
      getExtension(name: string) {
        return name === "WEBGL_debug_renderer_info" && scenario.unmasked !== null
          ? { UNMASKED_RENDERER_WEBGL: 2 }
          : null;
      },
      getParameter(parameter: number) {
        return parameter === 2 ? scenario.unmasked : scenario.plain;
      },
    };
    const document = {
      querySelector: () => ({ oversightGraph: { metrics: () => graphMetrics } }),
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
    try {
      const path = join(directory, "watch-dark.json");
      await writeSceneMetrics(page, path);
      assert.deepEqual(JSON.parse(await readFile(path, "utf8")), {
        ...graphMetrics,
        webglRenderer: scenario.expected,
      });
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  });
}
