import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";
import type { Browser } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

import type { runAnimation } from "./animationFrames.browser.fixture";

declare global {
  interface Window {
    runGraphAnimation: typeof runAnimation;
  }
}

let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let url = "";
let html = "";
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(
      new URL("../../../.shots/animation-frames-vite-cache", import.meta.url),
    ),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><script type="module">import "/src/theme/index.css"; import { runAnimation } from "/src/graph/animationFrames.browser.fixture.ts"; window.runGraphAnimation = runAnimation;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
}, 60_000);
afterAll(async () => {
  await browser?.close();
  await server?.close();
});

for (const kind of ["camera", "element"] as const) {
  it(`renders every native step of a real ${kind} animation, then stops`, async () => {
    if (!browser) throw new Error("Missing browser");
    const page = await browser.newPage();
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    try {
      await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
      await page.goto(url);
      await page.waitForFunction(() => typeof window.runGraphAnimation === "function");
      const result = await page.evaluate((kind) => window.runGraphAnimation(kind), kind);
      expect(result.rendersPerStep.length).toBeGreaterThan(2);
      expect(result.rendersPerStep.every((count) => count === 1)).toBe(true);
      expect(result.animationRenders).toBeGreaterThanOrEqual(result.rendersPerStep.length);
      expect(result.idleRenders).toBe(0);
      if (kind === "camera") expect(result.zoomRatio).toBeCloseTo(1.5);
      else expect(result.x).toBe(200);
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  }, 15_000);
}
