// Reproduce the watch comparison with `make test-browser`. Only the reference response lifts the
// cap to 1.5; production source stays capped at one. Both shots use the same browser DPR, viewport,
// graph, theme and frozen field clock, so only the field's drawing buffer changes.
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

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
    await page.route("**/src/graph/field/controller.ts*", async (route) => {
      const response = await route.fetch();
      const source = await response.text();
      const capped = "Math.min(window.devicePixelRatio || 1, 1)";
      expect(source.split(capped)).toHaveLength(2);
      await route.fulfill({
        response,
        body: source.replace(capped, "Math.min(window.devicePixelRatio || 1, 1.5)"),
      });
    });
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

it("captures the same watch scene with field buffers at DPR 1 and 1.5", async () => {
  if (!browser) throw new Error("Browser was not started");
  const metrics = [];
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
