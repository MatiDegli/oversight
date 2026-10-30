import { launchBrowser } from "../../../browserLaunch.support";
import { fileURLToPath } from "node:url";
import { expect as check } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import type {} from "./dock.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../../../.shots/dock-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><script type="module">import "/src/theme/index.css"; import { dockTest } from "/src/app/shell/dock/dock.browser.fixture.tsx"; window.dockTest = dockTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});
async function withPage(run: (page: Page) => Promise<void>) {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
    reducedMotion: "reduce",
  });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => !!window.dockTest);
    await page.evaluate(() => {
      window.dockTest.mount();
    });
    await run(page);
  } finally {
    expect(errors).toEqual([]);
    await page.close();
  }
}

it("has linked roving tabs, arrow/Home/End keys, empty states and a reserved main terminal", async () => {
  await withPage(async (page) => {
    const tabs = page.getByRole("tablist", { name: "Dock", exact: true }).getByRole("tab");
    await check(tabs).toHaveText(["Terminal", "Tests", "Activity log"]);
    await check(tabs.nth(1)).toHaveAttribute("tabindex", "0");
    await check(page.getByRole("tabpanel", { name: "Tests", exact: true })).toHaveText(
      "Test results will appear here.",
    );
    await tabs.nth(1).focus();
    for (const [key, name] of [
      ["ArrowRight", "Activity log"],
      ["ArrowRight", "Terminal"],
      ["ArrowLeft", "Activity log"],
      ["Home", "Terminal"],
      ["End", "Activity log"],
    ] as const) {
      await page.keyboard.press(key);
      const selected = page.getByRole("tab", { name, exact: true });
      await check(selected).toBeFocused();
      await check(selected).toHaveAttribute("aria-selected", "true");
      await check(page.getByRole("tabpanel")).toHaveAttribute(
        "id",
        (await selected.getAttribute("aria-controls")) ?? "",
      );
      await check(page.getByRole("tabpanel")).toHaveAttribute(
        "aria-labelledby",
        (await selected.getAttribute("id")) ?? "",
      );
    }
    await check(page.getByRole("tabpanel")).toHaveText("The run’s activity log will appear here.");
    await tabs.first().click();
    await check(page.getByRole("tabpanel")).toHaveText(
      "main · Terminal comes later. No shell is available.",
    );
    await check(page.getByRole("tabpanel").getByRole("textbox")).toHaveCount(0);
    await page.getByRole("button", { name: "Close dock" }).click();
    await check(page.getByLabel("Bottom dock", { exact: true })).toHaveCount(0);
  });
});

it("renders live counts, named failure icons and Mono data without moving focus or tabs", async () => {
  await withPage(async (page) => {
    const outside = page.getByLabel("Outside comment");
    await outside.focus();
    await page.evaluate(() => {
      window.dockTest.populate();
    });
    await check(outside).toBeFocused();
    await check(page.getByRole("tab", { name: "Tests", exact: true })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    const panel = page.getByRole("tabpanel");
    await check(panel).toContainText("8 passed");
    await check(panel).toContainText("1 failed");
    await check(panel).toContainText("1.43 s");
    await check(panel).toContainText("tests/test_cart.py::test_total");
    await check(panel.locator('svg[aria-label="Failed"]')).toHaveCount(1);
    expect(
      await panel
        .locator(".dock-number")
        .first()
        .evaluate((node) => getComputedStyle(node).fontFamily),
    ).toContain("Atkinson Hyperlegible Mono");
    await page.getByRole("tab", { name: "Activity log", exact: true }).click();
    await check(panel).toContainText("Proposed · change-1");
    await check(panel).toContainText("Approved · merged · change-1");
    await check(panel).toContainText("Error · tools");
    await check(panel).toContainText("No time");
    await check(panel.locator('.dock-letter[data-slot="A"]')).toHaveCount(3);
  });
});

it("follows new entries only at the bottom, keeps focus and caps long logs", async () => {
  await withPage(async (page) => {
    await page.getByRole("tab", { name: "Activity log", exact: true }).click();
    await page.evaluate(() => {
      window.dockTest.steps(80);
    });
    const scroll = page.getByLabel("Activity entries", { exact: true });
    const gap = () =>
      scroll.evaluate((node) => node.scrollHeight - node.clientHeight - node.scrollTop);
    await check.poll(gap).toBeLessThanOrEqual(2);
    await page.getByLabel("Outside comment").focus();
    await scroll.evaluate((node) => {
      node.scrollTop = 0;
      node.dispatchEvent(new Event("scroll", { bubbles: true }));
    });
    await page.evaluate(() => {
      window.dockTest.steps(1);
    });
    expect(await scroll.evaluate((node) => node.scrollTop)).toBe(0);
    await check(page.getByLabel("Outside comment")).toBeFocused();
    await scroll.evaluate((node) => {
      node.scrollTop = node.scrollHeight;
      node.dispatchEvent(new Event("scroll", { bubbles: true }));
    });
    await page.evaluate(() => {
      window.dockTest.steps(1);
    });
    await check.poll(gap).toBeLessThanOrEqual(2);
    await page.evaluate(() => {
      window.dockTest.steps(1000);
    });
    await check(page.locator(".dock-activity-row")).toHaveCount(300);
    await check(page.getByRole("tabpanel")).toContainText("omitted: 782");
    await check(page.locator(".dock-activity-row").first()).toContainText("Read 783");
    await check(page.locator(".dock-activity-row").last()).toContainText("Read 1082");
    await check.poll(gap).toBeLessThanOrEqual(2);
    await check(page.getByLabel("Outside comment")).toBeFocused();
  });
});
