import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect as check } from "@playwright/test";
import type { Browser } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type {} from "./modelWait.browser.fixture";

const shots = fileURLToPath(new URL("../../../.shots/model-retry-card/", import.meta.url));
let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: `${shots}vite-cache`,
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { modelWaitTest } from "/src/agents/modelWait.browser.fixture.tsx"; window.modelWaitTest = modelWaitTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

it.each(["dark", "day"])("preserves size and focus while waiting in %s", async (theme) => {
  const page = await browser.newPage({
    viewport: { width: 1920, height: 1080 },
    reducedMotion: "reduce",
  });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => !!window.modelWaitTest);
    await page.evaluate((name) => {
      document.documentElement.dataset.theme = name;
      window.modelWaitTest.mount(false);
    }, theme);
    await page.evaluate(() => document.fonts.ready);
    const cards = page.getByRole("article", { name: "Agent A", exact: true });
    const waiting = cards.nth(0),
      working = cards.nth(1);
    const focus = waiting.getByRole("button", { name: "Live diff", exact: true });
    await focus.focus();
    const boxes = await Promise.all([waiting.boundingBox(), working.boundingBox()]);
    await page.evaluate(() => {
      window.modelWaitTest.mount(true);
    });
    await check(waiting.getByText("Waiting", { exact: true })).toBeVisible();
    const line = waiting.getByText("Waiting for model (retry 3/5)", { exact: true });
    await check(line).toBeVisible();
    expect(await line.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
    await check(working.getByText("Working", { exact: true })).toBeVisible();
    await check(focus).toBeFocused();
    expect(await Promise.all([waiting.boundingBox(), working.boundingBox()])).toEqual(boxes);
    await check(page.getByLabel("Focus panel", { exact: true })).toHaveCount(0);
    expect(errors).toEqual([]);
    if (process.env.MODEL_RETRY_SHOTS === "1") {
      mkdirSync(shots, { recursive: true });
      await page.screenshot({ path: `${shots}${theme}.png` });
    }
    await page.evaluate(() => {
      document.documentElement.style.zoom = "2";
    });
    await check(line).toBeVisible();
    expect(await line.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
    await check(focus).toBeFocused();
  } finally {
    await page.close();
  }
});
