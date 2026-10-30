// Reproduce the watch comparison with `make test-browser`. Only the reference response lifts the
// cap to 1.5; production source stays capped at one. Both shots use the same browser DPR, viewport,
// graph, theme and frozen field clock, so only the field's drawing buffer changes.
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it, vi } from "vitest";

const out = fileURLToPath(new URL("../../../../.shots/field-cost/", import.meta.url));
let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let url: string;
let html: string;

beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../../", import.meta.url)),
    cacheDir: `${out}vite-cache`,
    server: {
      host: "127.0.0.1",
      port: 0,
      strictPort: false,
      fs: { allow: [fileURLToPath(new URL("../../../../", import.meta.url))] },
    },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { fieldTest } from "/src/app/dotField.browser.fixture.tsx"; window.fieldTest = fieldTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
  await mkdir(out, { recursive: true });
}, 60_000);

afterAll(async () => {
  await browser?.close();
  await server?.close();
});

async function capture(page: Page, ratio: number) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  if (ratio === 1.5) {
    const source = (await server?.transformRequest("/src/graph/field/controller.ts"))?.code ?? "";
    const capped = "Math.min(window.devicePixelRatio || 1, 1)";
    expect(
      source.split(capped),
      "Reference controller must contain exactly one DPR cap",
    ).toHaveLength(2);
    await page.route("**/src/graph/field/controller.ts*", (route) =>
      route.fulfill({
        contentType: "text/javascript",
        body: source.replace(capped, "Math.min(window.devicePixelRatio || 1, 1.5)"),
      }),
    );
  }
  await page.route(/\/?\?field-time=2$/, (route) =>
    route.fulfill({ contentType: "text/html", body: html }),
  );
  await page.goto(`${url}?field-time=2`);
  await page.waitForFunction(() => "fieldTest" in window);
  await page.evaluate(() => {
    window.fieldTest.mount("watch");
  });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(() => {
    const { graph, field } = window.fieldTest.elements();
    return (
      (graph.oversightGraph?.nodeCenters().length ?? 0) > 0 &&
      graph.oversightGraph?.animating() === false &&
      field.oversightField?.animating() === false
    );
  });
  const metrics = await page.evaluate(() => {
    const { graph, field } = window.fieldTest.elements();
    field.oversightField?.pixel(0, 0);
    return {
      browserDpr: window.devicePixelRatio,
      css: [field.clientWidth, field.clientHeight],
      buffer: [field.width, field.height],
      state: field.oversightField?.state(),
      nodes: graph.oversightGraph?.nodeCenters(),
    };
  });
  expect(metrics.state).toBe("webgl");
  expect(metrics.buffer).toEqual(metrics.css.map((size) => Math.round(size * ratio)));
  expect(errors).toEqual([]);
  await page.screenshot({ path: `${out}watch-dark-dpr-${String(ratio)}.png`, scale: "css" });
  await writeFile(
    `${out}watch-dark-dpr-${String(ratio)}.json`,
    JSON.stringify(metrics, null, 2) + "\n",
  );
  return metrics;
}

it.each(["missing", "duplicated"])("rejects a %s cap before navigation", async (shape) => {
  if (!browser || !server) throw new Error("Browser and server were not started");
  const page = await browser.newPage();
  const source = "Math.min(window.devicePixelRatio || 1, 1)";
  const transform = vi.spyOn(server, "transformRequest").mockResolvedValueOnce({
    code: shape === "missing" ? "" : `${source}; ${source}`,
    map: null,
  });
  const navigate = vi.spyOn(page, "goto").mockRejectedValue(new Error("Unexpected navigation"));
  try {
    await expect(capture(page, 1.5)).rejects.toThrow(
      "Reference controller must contain exactly one DPR cap",
    );
    expect(navigate).not.toHaveBeenCalled();
  } finally {
    transform.mockRestore();
    await page.close();
  }
});

it("captures the same watch scene with field buffers at DPR 1 and 1.5", async () => {
  if (!browser) throw new Error("Browser was not started");
  const metrics: Awaited<ReturnType<typeof capture>>[] = [];
  for (const ratio of [1, 1.5]) {
    const page = await browser.newPage({
      viewport: { width: 1920, height: 1080 },
      deviceScaleFactor: 1.5,
    });
    page.setDefaultTimeout(20_000);
    try {
      metrics.push(await capture(page, ratio));
    } finally {
      await page.close();
    }
  }
  expect(metrics[0]?.css).toEqual(metrics[1]?.css);
  expect(metrics[0]?.nodes).toEqual(metrics[1]?.nodes);
}, 90_000);

it.runIf(process.env.LIFE_SHOTS === "1")(
  "measures rest and travelling field frames under SwiftShader at 1920 by 1080",
  async () => {
    if (!browser) throw new Error("Browser was not started");
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    try {
      await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
      await page.goto(url);
      await page.waitForFunction(() => "fieldTest" in window);
      await page.evaluate(() => {
        window.fieldTest.mount("rest");
      });
      await page.evaluate(() => document.fonts.ready);
      await page.waitForFunction(() => {
        const { graph, field } = window.fieldTest.elements();
        return (
          graph.oversightGraph?.animating() === false && field.oversightField?.animating() === false
        );
      });
      const rest = await page.evaluate(() => {
        const probe = window.fieldTest.elements().field.oversightField;
        if (!probe) throw new Error("No field probe");
        const gl = document.createElement("canvas").getContext("webgl2");
        const info = gl?.getExtension("WEBGL_debug_renderer_info");
        const renderer = info ? String(gl?.getParameter(info.UNMASKED_RENDERER_WEBGL)) : "unknown";
        const times: number[] = [];
        for (let i = 0; i < 110; i++) {
          const start = performance.now();
          probe.pixel(0, 0);
          if (i >= 10) times.push(performance.now() - start);
        }
        return { renderer, times };
      });
      expect(rest.renderer).toMatch(/SwiftShader/);
      await page.evaluate(() => {
        window.fieldTest.mount("waiting");
      });
      const travel = await page.evaluate(async () => {
        // Let the mounted waiting scene settle before applying the real merge event.
        await new Promise<void>((resolve) =>
          requestAnimationFrame(() => {
            resolve();
          }),
        );
        window.fieldTest.advance(21);
        const probe = window.fieldTest.elements().field.oversightField;
        if (!probe) throw new Error("No field probe");
        const times: number[] = [];
        const end = performance.now() + 2_100;
        while (performance.now() < end) {
          const start = performance.now();
          probe.pixel(0, 0);
          if (probe.life().pulses > 0) times.push(performance.now() - start);
        }
        return times;
      });
      const summarize = (times: number[]) => {
        const sorted = [...times].sort((a, b) => a - b);
        return {
          samples: sorted.length,
          medianMs: sorted[Math.floor(sorted.length / 2)],
          p95Ms: sorted[Math.ceil(sorted.length * 0.95) - 1],
        };
      };
      expect(travel.length).toBeGreaterThan(10);
      const dir = fileURLToPath(new URL("../../../../.shots/life-look/r6/", import.meta.url));
      await mkdir(dir, { recursive: true });
      await writeFile(
        `${dir}cost-${process.env.LIFE_COST_LABEL ?? "tip"}.json`,
        JSON.stringify(
          {
            viewport: [1920, 1080],
            renderer: rest.renderer,
            method:
              "Forced complete field frame including GPU readback; same harness and scene on both revisions",
            rest: summarize(rest.times),
            travel: summarize(travel),
          },
          null,
          2,
        ) + "\n",
      );
    } finally {
      await page.close();
    }
  },
  90_000,
);
