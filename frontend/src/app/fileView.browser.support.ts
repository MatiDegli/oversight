import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { chromium, expect as browserExpect } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect } from "vitest";

import type {} from "./fileView.browser.fixture";
import type { GraphProbeHost } from "../graph";

let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let url: string;
let html: string;
export const original = readFileSync(
  new URL("../../../demo-repo/shop/cart.py", import.meta.url),
  "utf8",
);
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/files-vite-cache", import.meta.url)),
    server: {
      host: "127.0.0.1",
      port: 0,
      strictPort: false,
      fs: { allow: [fileURLToPath(new URL("../../../", import.meta.url))] },
    },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { fileViewTest } from "/src/app/fileView.browser.fixture.tsx"; window.fileViewTest = fileViewTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
}, 60_000);
afterAll(async () => {
  await browser?.close();
  await server?.close();
});

interface Save {
  path: string;
  content: string;
  base_hash: string;
}
export async function withPage(
  test: (page: Page, calls: Save[], serverFile: { content: string; hash: string }) => Promise<void>,
) {
  if (!browser) throw new Error("Browser was not started");
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  page.setDefaultTimeout(15_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const calls: Save[] = [];
  const serverFile = { content: original, hash: "h0" };
  try {
    // Keep the real App session idle at the seeded proposal, without a backend or SSE retries.
    await page.addInitScript(() => {
      Object.defineProperty(window, "EventSource", {
        value: class {
          onmessage = null;
          onerror = null;
          readyState = 1;
          close() {
            this.readyState = 2;
          }
        },
      });
    });
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.route("**/files?*", (route) =>
      route.fulfill({ json: { path: "shop/cart.py", ...serverFile, agent_id: null } }),
    );
    await page.route("**/files", async (route) => {
      const body = route.request().postDataJSON() as Save;
      calls.push(body);
      if (body.base_hash !== serverFile.hash) {
        await route.fulfill({
          status: 409,
          json: { error: { code: "stale_file", message: "The file moved." } },
        });
      } else {
        serverFile.content = body.content;
        serverFile.hash = `saved-${String(calls.length)}`;
        await route.fulfill({ json: { path: body.path, hash: serverFile.hash } });
      }
    });
    await page.goto(url);
    await page.waitForFunction(() => "fileViewTest" in window);
    await page.evaluate(() => {
      window.fileViewTest.mount();
    });
    await page.waitForFunction(
      () =>
        (document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.nodeCenters().length ?? 0) > 0,
    );
    // Cart is outside the hub-centered start view; navigate to it with the real fit control.
    await page.getByRole("button", { name: "Fit the graph to the view" }).click();
    // These tests begin after the person opens Cart; opening a file itself must move no rows.
    await clickGraphNode(page, "shop.cart:Cart");
    await page.waitForFunction(
      () =>
        document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.animating() === false,
    );
    await test(page, calls, serverFile);
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
}

export const filePanel = (page: Page) => page.getByRole("complementary", { name: "File view" });
// Chromium's Monaco types into `.native-edit-context` (EditContext). Its only textarea is the
// read-only `.ime-text-area` helper, which is never the input; other engines use `.inputarea`.
export const input = (page: Page) =>
  filePanel(page).locator(".monaco-editor :is(.native-edit-context, textarea.inputarea)");
export async function clickGraphNode(page: Page, id: string) {
  await page.waitForFunction(
    () =>
      document
        .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
        ?.oversightGraph?.animating() === false,
  );
  const point = await page.evaluate((wanted) => {
    const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const node = host?.oversightGraph?.nodeCenters().find(({ id }) => id === wanted);
    if (!host || !node) throw new Error("Graph node missing");
    const box = host.getBoundingClientRect();
    return { x: box.left + node.x, y: box.top + node.y };
  }, id);
  await page.mouse.click(point.x, point.y);
}
export async function clickUntouched(page: Page, phase = "clean") {
  const id = await page.evaluate(() => window.fileViewTest.untouched);
  await clickGraphNode(page, id);
  await browserExpect(filePanel(page)).toHaveAttribute("data-state", phase);
}
export async function append(page: Page, text: string) {
  await input(page).focus();
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText(`\n# ${text}`);
  await browserExpect(filePanel(page).getByText("Unsaved edits", { exact: true })).toBeVisible();
}
