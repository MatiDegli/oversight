import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type {} from "./chatReview.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: await mkdtemp(join(tmpdir(), "chat-start-panel-vite-")),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><div id="root" style="height:100vh"></div><script type="module">import "/src/theme/index.css"; import "/src/app/chatReview.browser.fixture.tsx";</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

async function openReviewWithSettings(page: Page) {
  await page
    .locator(".shell-top-bar")
    .getByRole("button", { name: /Decisions/ })
    .click();
  await page
    .locator(".inbox-row")
    .filter({ has: page.getByLabel("Agent A", { exact: true }) })
    .getByRole("button", { name: /Review change/ })
    .click();
  await check(page.getByRole("complementary", { name: "Review panel" })).toBeVisible();
  const settings = page.locator(".shell-top-bar").getByRole("button", {
    name: "Settings",
    exact: true,
  });
  await settings.click();
  const tabs = page.getByRole("tablist", { name: "Open files and changes" });
  const review = tabs.getByRole("tab", { name: /Review · change-1/ });
  await check(review).toBeVisible();
  const retained = await tabs.getByRole("tab").allTextContents();
  await review.click();
  return { settings, tabs, review, retained };
}

it.each(
  ["New task", "New lead"].flatMap((action) => ["Escape", "Alt+w"].map((key) => ({ action, key }))),
)("$action keeps hidden shared tabs through $key", async ({ action, key }) => {
  const page = await browser.newPage({
    viewport: { width: 1280, height: 800 },
    reducedMotion: "reduce",
  });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/events?*", (route) =>
    route.fulfill({ contentType: "text/event-stream", body: ": handwritten fixture\n\n" }),
  );
  try {
    await page.goto(url);
    await page.waitForFunction(() => !!window.chatReviewTest);
    await page.evaluate(() => {
      window.chatReviewTest.mount(true);
    });
    const { settings, tabs, review, retained } = await openReviewWithSettings(page);

    await page
      .locator(".chat-start-actions")
      .getByRole("button", { name: action, exact: true })
      .click();
    const input = page.getByRole("textbox", {
      name: action === "New task" ? "Task for the new agent" : "First task",
      exact: true,
    });
    await check(input).toBeFocused();
    await check(page.locator(".focus-panel")).toBeHidden();
    await page.evaluate(() => {
      window.chatReviewTest.questions();
    });
    await check(input).toBeFocused();
    await check(page.locator(".focus-panel")).toBeHidden();

    const draft = `Keep the ${action} draft through ${key}`;
    await input.fill(draft);
    const button =
      action === "New lead"
        ? page.getByRole("button", { name: "Start lead", exact: true })
        : page.locator(".chat-start-actions").getByRole("button", { name: action, exact: true });
    await button.focus();
    await button.press(key);
    await check(button).toBeFocused();
    await check(page.locator(".focus-panel")).toBeHidden();
    await check(input).toBeVisible();
    await check(input).toHaveValue(draft);

    const layouts = page.getByRole("group", { name: "Workspace view" });
    await layouts.getByRole("button", { name: "Oversight", exact: true }).click();
    await check(tabs.getByRole("tab")).toHaveText(retained);
    await check(review).toBeVisible();
    await check(review).toHaveAttribute("aria-selected", "true");
    await review.click();
    await layouts.getByRole("button", { name: "Chat", exact: true }).click();
    await check(page.getByRole("complementary", { name: "Review panel" })).toBeVisible();

    // Opening the already active Settings tab must also reveal the panel again.
    await settings.click();
    await page
      .locator(".chat-start-actions")
      .getByRole("button", { name: action, exact: true })
      .click();
    await check(input).toBeFocused();
    await settings.click();
    await check(tabs.getByRole("tab", { name: "Settings", exact: true })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await check(tabs.getByRole("tab")).toHaveText(retained);
    // The same key still closes the active tab once the panel is visible again.
    await settings.focus();
    await settings.press(key);
    await check(page.getByRole("complementary", { name: "Review panel" })).toBeVisible();
    await layouts.getByRole("button", { name: "Oversight", exact: true }).click();
    await check(tabs.getByRole("tab", { name: "Settings", exact: true })).toHaveCount(0);
    await check(tabs.getByRole("tab")).toHaveText(retained.slice(0, -1));
    await check(review).toHaveAttribute("aria-selected", "true");
  } finally {
    await page.close();
  }
});
