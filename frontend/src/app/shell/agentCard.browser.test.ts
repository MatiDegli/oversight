import { launchBrowser } from "../../browserLaunch.support";
import { fileURLToPath } from "node:url";
import { expect as check } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import type {} from "./agentCard.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../../.shots/agent-card-cache", import.meta.url)),
    server: {
      host: "127.0.0.1",
      port: 0,
      strictPort: false,
      fs: { allow: [fileURLToPath(new URL("../../../", import.meta.url))] },
    },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><script type="module">import "/src/theme/index.css"; import { readyTest } from "/src/app/shell/agentCard.browser.fixture.tsx"; window.readyTest = readyTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

async function outside(page: Page) {
  return page
    .locator(
      '.shell-frame > header, .graph-stage, .left-rail, [aria-label="Status line"], [aria-label="Agent B"], [aria-label="Task for the new agent"]',
    )
    .evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().toJSON() as object));
}

it.each(["task", "steps", "diff", "stop"] as const)(
  "ready preserves external layout and %s focus",
  async (target) => {
    const page = await browser.newPage({
      viewport: { width: 1920, height: 1080 },
      reducedMotion: "reduce",
    });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    try {
      await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
      await page.goto(url);
      await page.waitForFunction(() => !!window.readyTest);
      await page.evaluate(() => {
        window.readyTest.mount();
      });
      await page.evaluate(() => document.fonts.ready);
      const card = page.getByRole("article", { name: "Agent A", exact: true });
      const focus =
        target === "task"
          ? page.getByLabel("Task for the new agent")
          : target === "steps"
            ? card.getByRole("list")
            : card.getByRole("button", {
                name: target === "diff" ? "Live diff" : "Stop and discard",
                exact: true,
              });
      await focus.focus();
      const bDiff = page
        .getByRole("article", { name: "Agent B", exact: true })
        .getByRole("button", { name: "Live diff", exact: true });
      await check(bDiff).toBeInViewport();
      const before = await outside(page);
      const size = await card.boundingBox();
      await page.evaluate(() => {
        window.readyTest.ready();
      });
      await check(card.getByRole("status")).toHaveText("Ready for review");
      await check(card.getByRole("list")).toHaveCount(0);
      await check(card.getByRole("progressbar")).toHaveCount(0);
      await check(card.getByRole("button", { name: "Review change" })).toBeVisible();
      await check(card.getByRole("button", { name: "Live diff" })).toHaveCount(0);
      const typography = await card.locator("dd").evaluateAll((facts) => {
        const tokens = getComputedStyle(document.documentElement);
        const textFont = tokens.getPropertyValue("--text-font").trim();
        const monoFont = tokens.getPropertyValue("--code-font").trim();
        return facts.flatMap((fact) => {
          const walker = document.createTreeWalker(fact, NodeFilter.SHOW_TEXT);
          const wrong: string[] = [];
          while (walker.nextNode()) {
            const node = walker.currentNode;
            const text = node.textContent?.trim() ?? "";
            const parent = node.parentElement;
            if (!text || !parent || parent.closest("svg")) continue;
            const mono = /^(?:[+−-]|(?:<?US\$)?[+−-]?\d+(?:\.\d+)?)$/.test(text);
            if (getComputedStyle(parent).fontFamily !== (mono ? monoFont : textFont))
              wrong.push(text);
          }
          return wrong;
        });
      });
      expect(typography).toEqual([]);
      await check(target === "steps" || target === "diff" ? card : focus).toBeFocused();
      await check(bDiff).toBeInViewport();
      expect(await outside(page)).toEqual(before);
      expect(await card.boundingBox()).toEqual(size);
      await check(page.getByLabel("Focus panel", { exact: true })).toHaveCount(0);
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  },
  30_000,
);
