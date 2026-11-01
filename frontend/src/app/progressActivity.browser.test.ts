import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import type { GraphProbeHost } from "../graph";
import { launchBrowser } from "../browserLaunch.support";
import type {} from "./progressActivity.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/progress-activity-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><script type="module">import "/src/theme/index.css"; import { progressTest } from "/src/app/progressActivity.browser.fixture.tsx"; window.progressTest = progressTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});
const panel = (page: Page) =>
  page.getByRole("region", { name: "Agent A conversation", exact: true });
const fold = (page: Page) => panel(page).getByRole("button", { name: /6 earlier calls/ });
async function open(
  width = 1920,
  theme = "dark",
  moment: "working" | "retry" | "limits" = "working",
) {
  const page = await browser.newPage({
    viewport: { width, height: width === 1920 ? 1080 : 800 },
    reducedMotion: "reduce",
    timezoneId: "America/Argentina/Buenos_Aires",
  });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(url);
  await page.waitForFunction(() => !!window.progressTest);
  await page.evaluate(
    ({ moment, theme }) => {
      document.documentElement.dataset.theme = theme;
      window.progressTest.mount(moment);
    },
    { moment, theme },
  );
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(
    () =>
      !!document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]')?.oversightGraph,
  );
  await page
    .getByRole("article", { name: "Agent A", exact: true })
    .getByRole("button", { name: "Conversation", exact: true })
    .click();
  await check(panel(page).getByText("Working", { exact: true })).toBeVisible();
  await check(panel(page).getByText("Task from you", { exact: true })).toBeVisible();
  await check(panel(page).getByRole("textbox")).toBeVisible();
  if (moment === "retry")
    await check(panel(page).getByText("Waiting for model", { exact: true })).toBeVisible();
  if (moment === "limits")
    await check(panel(page).getByText(/time limit for this round/)).toBeVisible();
  return page;
}
it("folds calls and rounds with keyboard buttons, keeps newest last and preserves focus on events", async () => {
  const page = await open();
  try {
    await check(panel(page).getByText("Working", { exact: true })).toBeVisible();
    await check(fold(page)).toHaveAttribute("aria-expanded", "false");
    await check(fold(page)).toContainText("50 ms in tools");
    await fold(page).focus();
    await fold(page).press("Enter");
    await check(fold(page)).toBeFocused();
    await check(
      panel(page).getByText("The old text appears 2 times. Nothing was changed."),
    ).toBeVisible();
    const tools = await panel(page).locator("code").allTextContents();
    expect(tools.at(-1)).toBe("edit_file");
    await page.evaluate(() => {
      window.progressTest.advance("retry");
    });
    await check(fold(page)).toBeFocused();
    await check(panel(page).getByText("Waiting for model", { exact: true })).toBeVisible();
    await check(panel(page).getByText("Revising", { exact: true })).toBeVisible();
    await panel(page).getByRole("textbox").focus();
    const before = await page.getByRole("tab").allTextContents();
    await page.evaluate(() => {
      window.progressTest.advance("limits");
    });
    await check(panel(page).getByRole("textbox")).toBeFocused();
    await check(panel(page).getByText("Waiting for model", { exact: true })).toHaveCount(0);
    await check(panel(page).getByText(/time limit for this round/)).toBeVisible();
    expect(await page.getByRole("tab").allTextContents()).toEqual(before);
    const activeFold = panel(page).getByRole("button", { name: /7 earlier calls/ });
    await activeFold.focus();
    await page.evaluate(() => {
      window.progressTest.endRound();
    });
    await check(panel(page).getByRole("button", { name: /^Round 2 · stopped/ })).toBeFocused();
    await check(panel(page).getByText(/time limit for this round/)).toHaveCount(0);
  } finally {
    await page.close();
  }
});
it("keeps another tab active and focused as retry and limit events arrive", async () => {
  const page = await open();
  try {
    await page.getByRole("button", { name: /Open decisions inbox$/ }).click();
    const tab = page.getByRole("tab", { name: "Decisions", exact: true });
    await tab.focus();
    const tabs = await page.getByRole("tab").allTextContents();
    for (const moment of ["retry", "limits"] as const) {
      await page.evaluate((moment) => {
        window.progressTest.advance(moment);
      }, moment);
      await check(tab).toBeFocused();
      await check(tab).toHaveAttribute("aria-selected", "true");
      expect(await page.getByRole("tab").allTextContents()).toEqual(tabs);
    }
  } finally {
    await page.close();
  }
});
it.each([320, 360])("keeps notice text within its column in a %i px panel", async (width) => {
  const page = await open(1280, "day", "retry");
  try {
    await panel(page).evaluate((node, width) => {
      node.style.width = `${String(width)}px`;
    }, width);
    for (const moment of ["retry", "limits"] as const) {
      await page.evaluate((moment) => {
        window.progressTest.advance(moment);
      }, moment);
      const paragraphs = panel(page).locator('[role="listitem"] p');
      const overflow = await paragraphs.evaluateAll((nodes) =>
        nodes
          .filter((node) => node.scrollWidth > node.clientWidth + 1)
          .map((node) => node.textContent),
      );
      expect(overflow).toEqual([]);
    }
  } finally {
    await page.close();
  }
});
it.each([1920, 1280])(
  "captures progress at %i in both themes",
  async (width) => {
    if (process.env.PROGRESS_SHOTS !== "1") return;
    const shots = fileURLToPath(new URL("../../../.shots/progress-activity/", import.meta.url));
    await mkdir(shots, { recursive: true });
    for (const theme of ["dark", "day"]) {
      for (const name of ["working", "working-open", "retry", "limits"] as const) {
        const page = await open(width, theme, name === "working-open" ? "working" : name);
        try {
          if (name === "working-open") {
            await fold(page).click();
            await panel(page)
              .getByRole("list", { name: "Conversation timeline" })
              .evaluate((node) => {
                node.scrollTop = node.scrollHeight;
              });
          }
          expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
          if (name === "limits" && width === 1920 && theme === "dark") {
            const measures = await panel(page).evaluate((node) => ({
              row: node.querySelector('[class*="min-h-[40px]"]')?.getBoundingClientRect().height,
              notice: node.querySelector('[class*="min-h-[44px]"]')?.getBoundingClientRect().height,
              meter: node.querySelector('[class*="w-[150px]"]')?.getBoundingClientRect().width,
            }));
            await writeFile(`${shots}measurements.json`, JSON.stringify(measures));
          }
          await page.screenshot({ path: `${shots}${name}-${String(width)}-${theme}.png` });
        } finally {
          await page.close();
        }
      }
    }
  },
  60_000,
);
