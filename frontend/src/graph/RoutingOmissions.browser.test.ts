import { fileURLToPath } from "node:url";

import { chromium, expect as browserExpect } from "@playwright/test";
import { createServer } from "vite";
import { it } from "vitest";

import type { omissionHarness } from "./RoutingOmissions.browser.fixture";

declare global {
  interface Window {
    omissionHarness: typeof omissionHarness;
  }
}

it("remounts the live cone card with the last omission count without a new event", async () => {
  const server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/omissions-vite-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  const browser = await chromium.launch();
  try {
    const html = await server.transformIndexHtml(
      "/",
      '<!doctype html><html><body><script type="module">import { omissionHarness } from "/src/graph/RoutingOmissions.browser.fixture.tsx"; window.omissionHarness = omissionHarness;</script></body></html>',
    );
    await server.listen();
    const page = await browser.newPage();
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(server.resolvedUrls?.local[0] ?? "");
    await page.waitForFunction(() => !!window.omissionHarness);
    await page.evaluate(() => {
      window.omissionHarness.report(3);
    });
    const card = page.getByRole("region", { name: "Possibly affected" });
    await browserExpect(card.getByRole("status")).toHaveText(
      "+3 call routes omitted: no clear lane",
    );
    await page.evaluate(() => {
      window.omissionHarness.show(false);
    });
    await browserExpect(card).toHaveCount(0);
    await page.evaluate(() => {
      window.omissionHarness.show(true);
    });
    await browserExpect(card.getByRole("status")).toHaveText(
      "+3 call routes omitted: no clear lane",
    );
    await browserExpect(page.getByRole("status")).toHaveCount(1);
  } finally {
    await browser.close();
    await server.close();
  }
}, 60_000);
