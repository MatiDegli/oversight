import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect } from "@playwright/test";
import type { Browser } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type { mountLeadPlans } from "./leadPlans.browser.fixture";
declare global {
  interface Window {
    mountLeadPlans: typeof mountLeadPlans;
  }
}
let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/lead-plans-vite-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import {mountLeadPlans} from "/src/app/leadPlans.browser.fixture.tsx"; window.mountLeadPlans=mountLeadPlans;mountLeadPlans(2);</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

it.each(["dark", "day"])("lists three lead plans without stealing focus in %s", async (theme) => {
  const page = await browser.newPage({
    viewport: { width: 1920, height: 1080 },
    reducedMotion: "reduce",
  });
  const errors: string[] = [],
    commands: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (request.method() !== "GET") commands.push(request.url());
  });
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await expect(page.getByRole("button", { name: /^Decisions: 0 pending/ })).toBeVisible();
    await page.evaluate((t) => {
      document.documentElement.dataset.theme = t;
    }, theme);
    const chip = page.getByRole("button", { name: /^Decisions:/ });
    await chip.focus();
    await page.evaluate(() => {
      window.mountLeadPlans(8);
    });
    await expect(chip).toHaveAccessibleName("Decisions: 3 pending. Open decisions inbox");
    await expect(chip).toBeFocused();
    await expect(page.getByRole("region", { name: "Decisions inbox" })).toHaveCount(0);
    await chip.click();
    const inbox = page.getByRole("region", { name: "Decisions inbox" });
    await expect(inbox).toBeVisible();
    await expect(inbox.getByRole("article")).toHaveCount(3);
    for (const name of [
      "Plan 1: update pricing",
      "Plan 2: update inventory",
      "Plan 3: update orders",
    ])
      await expect(inbox.getByRole("heading", { name, exact: true })).toBeVisible();
    await expect(page.getByLabel("Repository graph", { exact: true })).toBeVisible();
    if (process.env.LEAD_PLANS_SHOTS === "1") {
      await page.evaluate(() => document.fonts.ready);
      const shots = fileURLToPath(new URL("../../../.shots/lead-plans/", import.meta.url));
      await mkdir(shots, { recursive: true });
      await page.screenshot({ path: `${shots}${theme}.png` });
    }
    const decisions = inbox.getByRole("button", { name: /^Decisions [23]$/ });
    await decisions.focus();
    await page.evaluate(() => {
      window.mountLeadPlans(9);
    });
    await expect(inbox.getByRole("article")).toHaveCount(2);
    await expect(inbox.getByRole("heading", { name: "Plan 2: update inventory" })).toHaveCount(0);
    await expect(decisions).toHaveAccessibleName("Decisions 2");
    await expect(decisions).toBeFocused();
    expect(errors).toEqual([]);
    expect(commands).toEqual([]);
  } finally {
    await page.close();
  }
});
