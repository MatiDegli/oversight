import { mkdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect, type Browser } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type { GraphProbeHost } from "../graph";
import { openOversight } from "../workspaceView.browser.support";

let server: ViteDevServer, browser: Browser, url: string, stream: string;
beforeAll(async () => {
  // Serve the production entry unchanged, with scripted SSE and no backend or provider.
  const log = await readFile(
    new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
    "utf8",
  );
  stream = log
    .trim()
    .split("\n")
    .map((line) => `data: ${line}\n\n`)
    .join("");
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/chat-default-cache", import.meta.url)),
    envDir: false,
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

it.each([1280, 390].flatMap((width) => ["dark", "day"].map((theme) => ({ width, theme }))))(
  "production opens in Chat at $width px in $theme, then loads Oversight once",
  async ({ width, theme }) => {
    const page = await browser.newPage({
      viewport: { width, height: width === 390 ? 844 : 800 },
      reducedMotion: "reduce",
    });
    const requests: string[] = [],
      errors: string[] = [];
    page.on("request", (request) => requests.push(new URL(request.url()).pathname));
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/events?*", (route) =>
      route.fulfill({
        contentType: "text/event-stream",
        body: stream,
      }),
    );
    try {
      await page.goto(url);
      await page.evaluate((theme) => {
        document.documentElement.dataset.theme = theme;
      }, theme);
      const selector = page.getByRole("group", { name: "Workspace view", includeHidden: true });
      const chat = selector.getByRole("button", { name: "Chat", exact: true, includeHidden: true });
      await expect(chat).toHaveAttribute("aria-pressed", "true");
      await expect(page.locator(".chat-shell")).toHaveCount(1);
      await expect(page.getByLabel("Repository graph", { exact: true })).toHaveCount(0);
      expect(new URL(page.url()).search).toBe("");
      const graphRequests = () =>
        requests.filter((path) =>
          /GraphView|DotField|cytoscape|\/field\/controller|OversightStage/.test(path),
        );
      expect(graphRequests()).toEqual([]);
      await page.evaluate(() => document.fonts.ready);
      const directory = fileURLToPath(new URL("../../../.shots/chat-default/", import.meta.url));
      await mkdir(directory, { recursive: true });
      await page.screenshot({ path: `${directory}first-${String(width)}-${theme}.png` });
      await expect(page.locator(".chat-shell")).toBeVisible();
      await openOversight(page);
      if (width === 390) {
        await expect(page.locator(".narrow-notice")).toBeVisible();
        await page.setViewportSize({ width: 1280, height: 800 });
      }
      const graph = page.getByLabel("Repository graph", { exact: true });
      await expect(graph).toBeVisible();
      await page.waitForFunction(
        () =>
          (document
            .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph?.nodeCenters().length ?? 0) > 0,
      );
      await expect(
        selector.getByRole("button", { name: "Oversight", exact: true }),
      ).toHaveAttribute("aria-pressed", "true");
      expect(graphRequests().some((path) => path.includes("GraphView"))).toBe(true);
      const once = graphRequests();
      const host = await graph.elementHandle();
      await chat.click();
      await openOversight(page);
      expect(graphRequests()).toEqual(once);
      expect(
        await host.evaluate(
          (element) => element === document.querySelector('[aria-label="Repository graph"]'),
        ),
      ).toBe(true);
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  },
);
