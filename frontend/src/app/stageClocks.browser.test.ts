import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";

let browser: Browser;
let server: ViteDevServer;
let html: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><div id="root"></div><script type="module">import "/src/theme/index.css"; import "/src/app/stageClocks.browser.fixture.tsx";</script></body></html>',
  );
  await server.listen();
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

it.each([390, 1280, 1920])(
  "preserves stage metadata, actions and wrapping at %spx in both themes",
  async (width) => {
    const directory = fileURLToPath(new URL("../../../.shots/stage-clocks/", import.meta.url));
    await mkdir(directory, { recursive: true });
    for (const theme of ["dark", "day"]) {
      const page = await browser.newPage({
        viewport: { width, height: width === 390 ? 844 : 1080 },
        reducedMotion: "reduce",
      });
      try {
        await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
        await page.goto(server.resolvedUrls?.local[0] ?? "");
        await page.evaluate((theme) => {
          document.documentElement.dataset.theme = theme;
        }, theme);
        await page.evaluate(() => document.fonts.ready);
        const chat = page.getByRole("article", { name: "Proposed change" });
        const header = page.getByRole("region", { name: "Review header" });
        await check(chat).toBeVisible();
        for (const surface of [chat, header]) {
          await check(surface.getByRole("group", { name: "Stage times" })).toBeVisible();
          for (const name of [
            "Agent work, 4 hours 12 minutes 30 seconds",
            "Automatic checks, 50 minutes 30 seconds",
            "Waiting for your decision, 2 hours 31 minutes 30 seconds",
            "Test runs, 1 hour 41 minutes",
          ]) {
            await check(surface.getByRole("group", { name, exact: true })).toBeVisible();
          }
          await check(surface.getByText("1 revision round", { exact: true })).toBeVisible();
          await check(
            surface.getByRole("list", { name: "Facts" }).getByRole("listitem"),
          ).toHaveCount(4);
        }
        await check(chat.getByText("Cost US$0.03", { exact: true })).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
        expect(await chat.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
        const columns = await chat
          .locator(".chat-example-pair")
          .evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(" ").length);
        expect(columns).toBe(width === 390 ? 1 : 3);
        if (width === 390) {
          expect(
            await page.evaluate(() => document.documentElement.scrollHeight > innerHeight),
          ).toBe(true);
        }
        const review = chat.getByRole("button", { name: "Review change", exact: true });
        await review.scrollIntoViewIfNeeded();
        await check(review).toBeVisible();
        await review.focus();
        await check(review).toBeFocused();
        await page.screenshot({
          path: `${directory}${String(width)}-${theme}.png`,
          fullPage: true,
        });
        await review.click();
        await check(chat.getByText("Reviewing in the panel")).toBeVisible();
        await check(chat.getByRole("group", { name: "Stage times" })).toBeVisible();
        expect(await chat.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
        await page.screenshot({
          path: `${directory}${String(width)}-${theme}-compact.png`,
          fullPage: true,
        });
        await header.getByRole("button", { name: "Close review" }).click();
        await check(review).toBeVisible();
      } finally {
        await page.close();
      }
    }
  },
);
