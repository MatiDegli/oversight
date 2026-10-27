import { fileURLToPath } from "node:url";

import { chromium, expect as browserExpect } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

import type { GraphProbeHost } from "./probe";
import type { foldTest } from "./folds.browser.fixture";

declare global {
  interface Window {
    foldTest: typeof foldTest;
  }
}
let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let url = "";
let html = "";
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/folds-vite-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body style="margin:0"><script type="module">import "/src/theme/index.css"; import { foldTest } from "/src/graph/folds.browser.fixture.tsx"; window.foldTest = foldTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
}, 60_000);
afterAll(async () => {
  await browser?.close();
  await server?.close();
});

async function report(page: Page) {
  return page.evaluate(() => {
    const probe = document.querySelector<GraphProbeHost>(
      '[aria-label="Repository graph"]',
    )?.oversightGraph;
    if (!probe) throw new Error("Missing graph probe");
    return {
      positions: probe.positions(),
      centers: probe.nodeCenters(),
      animating: probe.animating(),
      pan: probe.pan(),
      zoom: probe.zoom(),
    };
  });
}
async function clickClass(page: Page) {
  const r = await report(page);
  const center = r.centers.find((p) => p.id === "Cart");
  if (!center) throw new Error("Missing class row");
  const y = await page.evaluate(() => {
    const probe = document.querySelector<GraphProbeHost>(
      '[aria-label="Repository graph"]',
    )?.oversightGraph;
    const bound = probe?.nodeBounds(".class").find((b) => b.id === "Cart");
    const folded = probe?.positions().Cart;
    return !folded && probe && bound ? (bound.y1 + 22) * probe.zoom() + probe.pan().y : null;
  });
  await page.mouse.click(center.x, y ?? center.y);
}
async function settled(page: Page) {
  await browserExpect.poll(async () => (await report(page)).animating).toBe(false);
}

for (const motion of ["no-preference", "reduce"] as const) {
  it(`class rows slide only their column for 240ms, or jump with ${motion}`, async () => {
    if (!browser) throw new Error("Missing browser");
    const page = await browser.newPage({
      viewport: { width: 1400, height: 900 },
      reducedMotion: motion,
    });
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    try {
      await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
      await page.goto(url);
      await page.waitForFunction(
        () =>
          !!document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph,
      );
      const before = await report(page);
      expect(before.positions["Cart:add"]).toBeUndefined();
      const start = await page.evaluate(() => performance.now());
      await clickClass(page);
      const first = await report(page);
      expect(first.animating).toBe(motion !== "reduce");
      await settled(page);
      const elapsed = (await page.evaluate(() => performance.now())) - start;
      const open = await report(page);
      expect(open.positions["Cart:add"]).toBeDefined();
      expect((open.positions.after?.y ?? 0) - (before.positions.after?.y ?? 0)).toBeGreaterThan(70);
      expect(open.positions.compute).toEqual(before.positions.compute);
      expect(open.pan).toEqual(before.pan);
      expect(open.zoom).toBe(before.zoom);
      if (motion === "no-preference") {
        expect(first.positions.after?.y).not.toBe(open.positions.after?.y);
        expect(first.positions.Tail?.y).not.toBe(open.positions.Tail?.y);
        expect(elapsed).toBeGreaterThanOrEqual(220);
        expect(elapsed).toBeLessThan(1000);
      } else expect(first.positions).toEqual(open.positions);
      // A second click restores precisely the same positions, not an accumulated offset.
      await clickClass(page);
      await settled(page);
      expect((await report(page)).positions).toEqual(before.positions);
      // A changed method opens it; folding again survives an unrelated state update.
      await page.evaluate(() => {
        window.foldTest.edit();
      });
      await settled(page);
      expect((await report(page)).positions).toEqual(open.positions);
      await clickClass(page);
      await settled(page);
      await page.evaluate(() => {
        window.foldTest.tick();
      });
      expect((await report(page)).positions).toEqual(before.positions);
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  }, 30_000);
}
