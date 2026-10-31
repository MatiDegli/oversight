import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    mode: "replay",
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><div id="root" style="height:100vh"></div><script type="module">import "/src/theme/index.css"; import "/src/app/startupSplit.browser.fixture.tsx";</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

it.each([
  ["review", "/src/review/ReviewPanel.tsx", "Review panel", ".review-body"],
  ["inbox", "/src/review/DecisionInbox.tsx", "Decisions inbox", ".inbox-entries"],
  [
    "conversation",
    "/src/agents/conversation/ConversationPanel.tsx",
    "Agent A conversation",
    '[aria-label="Conversation timeline"]',
  ],
  ["file-view", "/src/editor/implementation.ts", "File view", ".monaco-editor"],
  [
    "practice",
    "/src/app/challenge/PracticePanel.tsx",
    "Two-change practice",
    "button:has-text('Start practice')",
  ],
])(
  "keeps the %s frame on its first cold open",
  async (tab, module, label, content) => {
    const page = await browser.newPage({
      viewport: { width: 1920, height: 1080 },
      reducedMotion: "reduce",
    });
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route(`**${module}*`, async (route) => {
      await gate;
      await route.continue();
    });
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    try {
      await page.goto(url);
      await check(page.getByLabel("Repository graph", { exact: true })).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      if (tab === "review")
        await page.getByRole("button", { name: "Review change", exact: true }).first().click();
      if (tab === "inbox") await page.getByRole("button", { name: /^Decisions:/ }).click();
      if (tab === "conversation")
        await page
          .getByRole("article", { name: "Agent A", exact: true })
          .getByRole("button", { name: "Conversation", exact: true })
          .click();
      if (tab === "practice")
        await page.getByRole("button", { name: "Two-change practice", exact: true }).click();
      if (tab === "file-view") {
        await page.getByRole("button", { name: "Files", exact: true }).click();
        await page.getByRole("treeitem", { name: "shop", exact: true }).click();
        await page.getByRole("treeitem", { name: "pricing.py", exact: true }).click();
      }
      const panel = page.getByLabel(label, { exact: true });
      await check(panel).toBeVisible();
      await check(panel.locator(content)).toHaveCount(0);
      const before = await panel.boundingBox();
      const heading = panel.locator(tab === "practice" ? "h1" : "header").first();
      const header = await heading.boundingBox();
      if (process.env.STARTUP_SPLIT_SHOTS === "1") {
        const directory = fileURLToPath(new URL("../../.shots/startup-split/", import.meta.url));
        await mkdir(directory, { recursive: true });
        await page.screenshot({ path: `${directory}/${tab}-first-open.png` });
      }
      release();
      await check(panel.locator(content).first()).toBeVisible();
      expect(await panel.boundingBox()).toEqual(before);
      expect(await heading.boundingBox()).toEqual(header);
      expect(errors).toEqual([]);
    } finally {
      release();
      await page.close();
    }
  },
  30_000,
);
