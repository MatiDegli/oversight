import { fileURLToPath } from "node:url";
import { expect as check, type Browser } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type {} from "./composerTabs.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/composer-tabs-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><div id="root" style="height:100vh"></div><script type="module">import "/src/theme/index.css"; import "/src/app/composerTabs.browser.fixture.tsx";</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

it("restores a draft after a tab unmount and confirms a successful pause", async () => {
  const page = await browser.newPage({
    viewport: { width: 1920, height: 1080 },
    reducedMotion: "reduce",
  });
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => !!window.composerTabsTest);
    await page.evaluate(() => {
      window.composerTabsTest.mount();
    });
    const message = page.getByRole("textbox", { name: "Message Agent A", exact: true });
    const draft = "  Keep this unsent draft.\nAnd this line.  ";
    await message.fill(draft);
    const original = await message.elementHandle();

    const decisions = page.getByRole("tab", { name: "Decisions", exact: true });
    await decisions.click();
    await check(decisions).toHaveAttribute("aria-selected", "true");
    await check(page.locator("textarea")).toHaveCount(0);
    expect(await original.evaluate((element) => element.isConnected)).toBe(false);

    const conversation = page.getByRole("tab", { name: /Agent A · conversation/ });
    await conversation.click();
    await check(conversation).toHaveAttribute("aria-selected", "true");
    await check(message).toBeVisible();
    await check(message).toHaveValue(draft);
    const returned = await message.elementHandle();
    expect(await returned.evaluate((element, previous) => element === previous, original)).toBe(
      false,
    );
    expect(await original.evaluate((element) => element.isConnected)).toBe(false);

    await page.getByRole("button", { name: "Pause", exact: true }).click();
    const pausing = page.getByRole("button", { name: "Pausing after this step", exact: true });
    await check(pausing).toBeVisible();
    await check(pausing).toBeDisabled();
    expect(await page.evaluate(() => window.composerTabsTest.calls())).toEqual([
      { command: "pause", id: "agent-1" },
    ]);
  } finally {
    await page.close();
  }
});
