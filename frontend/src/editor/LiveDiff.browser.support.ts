import { fileURLToPath } from "node:url";

import { chromium, expect as browserExpect } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect } from "vitest";

import type { LiveDiffData } from "../state";
import type {} from "./LiveDiff.browser.fixture";

let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let url: string;
let html: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/editor-vite-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { panelTest } from "/src/editor/LiveDiff.browser.fixture.tsx"; window.panelTest = panelTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
}, 60_000);
afterAll(async () => {
  await browser?.close();
  await server?.close();
});

export async function withPage(test: (page: Page) => Promise<void>) {
  if (!browser) throw new Error("Browser was not started");
  const page = await browser.newPage({ viewport: { width: 1200, height: 1000 } });
  page.setDefaultTimeout(15_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  // Vite's client logs that it has no HMR socket here; every other console error fails the test.
  page.on("console", (message) => {
    if (message.type() === "error" && !message.location().url.endsWith("/@vite/client"))
      errors.push(message.text());
  });
  try {
    // Avoid starting the application's transport: only the editor fixture is mounted.
    await page.route("**/", (route) =>
      route.fulfill({
        contentType: "text/html",
        body: html,
      }),
    );
    await page.goto(url);
    await page.waitForFunction(() => "panelTest" in window);
    await test(page);
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
}

export const data: LiveDiffData = {
  changeId: "change-1",
  agentId: "agent-1",
  path: "shop/first.py",
  original: Array.from({ length: 51 }, (_, i) => `first_${String(i)} = ${String(i)}`).join("\n"),
  modified: Array.from(
    { length: 51 },
    (_, i) => `first_${String(i)} = ${String(i === 7 ? 99 : i)}`,
  ).join("\n"),
  linesAdded: 1,
  linesRemoved: 1,
  status: "modified",
  author: "agent",
};

export async function render(page: Page, input = data) {
  await page.evaluate((value) => {
    window.panelTest.render(value);
  }, input);
}
export async function expectDiff(page: Page, line: number, end = line) {
  await browserExpect
    .poll(
      () =>
        page.evaluate(() =>
          window.panelTest
            .current()
            ?.getLineChanges()
            ?.map((change) => [
              change.originalStartLineNumber,
              change.originalEndLineNumber,
              change.modifiedStartLineNumber,
              change.modifiedEndLineNumber,
            ]),
        ),
      { timeout: 15_000 },
    )
    .toEqual([[line, end, line, end]]);
}

export async function expectModels(page: Page, count: number) {
  await browserExpect.poll(() => page.evaluate(() => window.panelTest.models())).toBe(count);
}
