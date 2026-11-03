import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve, extname } from "node:path";
import { build } from "vite";
import { expect, test, type Page } from "@playwright/test";
import type * as fixture from "../src/app/workflowPanel.browser.fixture";

type WorkflowWindow = Window & { workflowTest: typeof fixture };
const site = fileURLToPath(new URL("../../.shots/workflow-panel/site", import.meta.url));
const frontend = fileURLToPath(new URL("../", import.meta.url));
const captures = fileURLToPath(new URL("../../.shots/workflow-panel/", import.meta.url));
test.use({ reducedMotion: "reduce" });
test.beforeAll(async () => {
  // A separate production bundle of the synthetic harness. The public recording stays intact.
  await build({
    configFile: resolve(frontend, "vite.config.ts"),
    root: frontend,
    mode: "production",
    define: { "process.env.NODE_ENV": JSON.stringify("production") },
    base: "/workflow-harness/",
    logLevel: "warn",
    build: {
      outDir: site,
      emptyOutDir: true,
      lib: {
        entry: resolve(frontend, "src/app/workflowPanel.browser.fixture.tsx"),
        formats: ["es"],
        fileName: "workflow",
        cssFileName: "workflow",
      },
    },
  });
});

async function open(page: Page, options: fixture.WorkflowOptions = {}, theme = "dark") {
  await page.route("**/workflow-harness/**", async (route) => {
    const name = new URL(route.request().url()).pathname.slice("/workflow-harness/".length);
    if (!name || name === "index.html") {
      await route.fulfill({
        contentType: "text/html",
        body: `<!doctype html><html data-theme="${theme}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/workflow-harness/workflow.css"></head><body><script type="module">import * as fixture from '/workflow-harness/workflow.js'; window.workflowTest=fixture; await fixture.mountWorkflow(${JSON.stringify(options)});</script></body></html>`,
      });
      return;
    }
    const file = resolve(site, name);
    if (!file.startsWith(`${site}/`)) {
      await route.abort();
      return;
    }
    const types: Record<string, string> = {
      ".js": "text/javascript",
      ".css": "text/css",
      ".woff2": "font/woff2",
    };
    await route.fulfill({
      body: await readFile(file),
      contentType: types[extname(file)] ?? "application/octet-stream",
    });
  });
  await page.goto("/workflow-harness/");
  await expect(
    page.getByRole("button", { name: "Open workflow", exact: true }).first(),
  ).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}
const panel = (page: Page) => page.getByRole("region", { name: "Workflow", exact: true });
const steps = (page: Page) =>
  panel(page).getByRole("list", { name: "Workflow steps" }).getByRole("button");

async function contrast(page: Page) {
  return panel(page).evaluate((root) => {
    const rgb = (color: string) => (color.match(/[\d.]+/g) ?? []).map(Number);
    const luminance = (color: number[]) =>
      color.slice(0, 3).reduce((sum, channel, i) => {
        const value = channel / 255;
        return (
          sum +
          (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4) *
            ([0.2126, 0.7152, 0.0722][i] ?? 0)
        );
      }, 0);
    const background = (node: Element): number[] => {
      const color = rgb(getComputedStyle(node).backgroundColor);
      if (color.length === 3 || color[3] === 1) return color;
      return node.parentElement ? background(node.parentElement) : [255, 255, 255];
    };
    const ratio = (a: number[], b: number[]) => {
      const x = luminance(a),
        y = luminance(b);
      return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
    };
    const text = [
      ...root.querySelectorAll(
        "h2,h3,h4,button,button span,dt,dd,.workflow-word,.workflow-team,.workflow-gates span,.workflow-gates strong,p",
      ),
    ]
      .filter((node) => node.getBoundingClientRect().width > 0)
      .map((node) => ratio(rgb(getComputedStyle(node).color), background(node)));
    const boundaries = [...root.querySelectorAll("button")].map((node) =>
      ratio(rgb(getComputedStyle(node).borderTopColor), background(node)),
    );
    const team = root.querySelector(".workflow-team > span");
    if (team?.parentElement)
      boundaries.push(
        ratio(rgb(getComputedStyle(team).backgroundColor), background(team.parentElement)),
      );
    return { text: Math.min(...text), graphics: Math.min(...boundaries) };
  });
}
async function evidence(page: Page) {
  const nodes = await steps(page).allTextContents();
  const details: string[] = [];
  for (const step of await steps(page).all()) {
    await step.click();
    details.push(await panel(page).getByRole("region", { name: "Step detail" }).innerText());
  }
  return { nodes, details };
}

test("production live ingestion and replay show identical nodes and details at equal prefixes", async ({
  page,
}) => {
  await open(page, { scene: "failed", mode: "live", prefix: 10 });
  await page.getByRole("button", { name: "Open workflow", exact: true }).click();
  const through = await page.evaluate(() => (window as WorkflowWindow).workflowTest.prefixLength());
  for (const prefix of [10, through - 3, through]) {
    await page.evaluate((count) => {
      (window as WorkflowWindow).workflowTest.ingestThrough(count);
    }, prefix);
    await expect
      .poll(() => page.evaluate(() => (window as WorkflowWindow).workflowTest.currentSeq()))
      .toBe(prefix);
    const live = await evidence(page);
    // Fresh replay transport parses/folds the whole inclusive prefix through production applyEvent.
    await page.evaluate(async (count) => {
      await (window as WorkflowWindow).workflowTest.replayThrough(count);
    }, prefix);
    await expect
      .poll(() => page.evaluate(() => (window as WorkflowWindow).workflowTest.currentSeq()))
      .toBe(prefix);
    expect(await evidence(page)).toEqual(live);
    // Start a fresh live ingestion for the next comparison, through the same public transport.
    await page.evaluate(async (count) => {
      await (window as WorkflowWindow).workflowTest.mountWorkflow({
        scene: "failed",
        mode: "live",
        prefix: count,
      });
    }, prefix);
    await page.getByRole("button", { name: "Open workflow", exact: true }).click();
  }
});

test("built replay retains native keyboard order and restores the launcher on Escape", async ({
  page,
}) => {
  await open(page, { mode: "replay" });
  const launcher = page.getByRole("button", { name: "Open workflow", exact: true });
  await launcher.focus();
  await launcher.press("Enter");
  const close = panel(page).getByRole("button", { name: "Close", exact: true });
  await expect(close).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(steps(page).nth(0)).toBeFocused();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Space");
  await expect(steps(page).nth(1)).toHaveAttribute("aria-pressed", "true");
  for (const i of [2, 3]) {
    await page.keyboard.press("Tab");
    await expect(steps(page).nth(i)).toBeFocused();
  }
  await page.keyboard.press("Tab");
  await expect(panel(page).getByRole("button", { name: "Review change" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(steps(page).nth(3)).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(panel(page)).toHaveCount(0);
  await expect(launcher).toBeFocused();
  await expect(page.getByLabel("Repository graph", { exact: true })).toHaveCount(0);
});

test("built arrival paths retain selection, scroll and focus and clear a removed step", async ({
  page,
}) => {
  await open(page, { scene: "large" });
  const launcher = page.getByRole("button", { name: "Open workflow", exact: true });
  await launcher.focus();
  await page.evaluate(() => {
    (window as WorkflowWindow).workflowTest.arrive();
  });
  await expect(panel(page)).toHaveCount(0);
  await expect(launcher).toBeFocused();
  await launcher.click();
  await steps(page).nth(3).click();
  const scroll = panel(page).locator(".workflow-body");
  await scroll.evaluate((node) => {
    node.scrollTop = 80;
  });
  const before = await scroll.evaluate((node) => node.scrollTop);
  await page.evaluate(() => {
    (window as WorkflowWindow).workflowTest.arrive();
  });
  await expect(steps(page)).toHaveCount(8);
  await expect(steps(page).nth(3)).toHaveAttribute("aria-pressed", "true");
  await expect(steps(page).nth(3)).toBeFocused();
  expect(await scroll.evaluate((node) => node.scrollTop)).toBe(before);
  await page.evaluate(() => {
    (window as WorkflowWindow).workflowTest.removeSelected();
  });
  await expect(steps(page)).toHaveCount(2);
  await expect(panel(page).getByRole("button", { name: "Close", exact: true })).toBeFocused();
  await expect(panel(page).getByRole("button", { pressed: true })).toHaveCount(0);
});

test("built inbox launcher retains the inbox and the repository graph", async ({ page }) => {
  await open(page, { layout: "Oversight", inbox: true, mode: "replay" });
  const launcher = page.getByRole("button", { name: "Open workflow", exact: true }).first();
  await launcher.click();
  await expect(panel(page)).toBeVisible();
  await expect(page.getByLabel("Repository graph", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Decisions inbox" })).toBeHidden();
  await panel(page).getByRole("button", { name: "Close", exact: true }).click();
  await expect(launcher).toBeFocused();
  await expect(page.getByRole("region", { name: "Decisions inbox" })).toBeVisible();
});

test("built evidence withdraws a focused Review action without losing focus", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Open workflow", exact: true }).click();
  await steps(page).nth(1).click();
  const review = panel(page).getByRole("button", { name: "Review change" });
  await review.focus();
  await page.evaluate(() => {
    (window as WorkflowWindow).workflowTest.settleReview();
  });
  await expect(review).toBeFocused();
  await expect(review).toBeDisabled();
  await review.press("Space");
  expect(await page.evaluate(() => (window as WorkflowWindow).workflowTest.navigation)).toEqual([]);
  await review.press("Tab");
  await expect(review).toHaveCount(0);
});

for (const layout of ["Chat", "Oversight"] as const)
  for (const width of [1280, 1920])
    for (const theme of ["dark", "day"]) {
      test(`built ${layout} ${String(width)} ${theme} has legible controls and no overflow`, async ({
        page,
      }) => {
        await page.setViewportSize({ width, height: width === 1920 ? 1080 : 900 });
        await open(page, { layout }, theme);
        await page.getByRole("button", { name: "Open workflow", exact: true }).click();
        await steps(page).nth(1).click();
        await steps(page).nth(1).press("Shift+Tab");
        await expect(steps(page).nth(0)).toBeFocused();
        expect(
          await steps(page)
            .nth(0)
            .evaluate((node) => {
              const style = getComputedStyle(node);
              return [style.outlineWidth, style.outlineOffset, style.outlineStyle];
            }),
        ).toEqual(["2px", "2px", "solid"]);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
        expect(await panel(page).evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(
          true,
        );
        const ratios = await contrast(page);
        expect(ratios.text).toBeGreaterThanOrEqual(4.5);
        expect(ratios.graphics).toBeGreaterThanOrEqual(3);
        if (process.env.WORKFLOW_SHOTS === "1") {
          await mkdir(captures, { recursive: true });
          await writeFile(
            resolve(captures, `${layout}-${String(width)}-${theme}.json`),
            JSON.stringify(
              {
                provenance: "Synthetic scripted events rendered by a production build",
                width,
                theme,
                layout,
                contrast: ratios,
              },
              null,
              2,
            ),
          );
          await page.screenshot({
            path: resolve(captures, `${layout}-${String(width)}-${theme}.png`),
            animations: "disabled",
          });
          await panel(page).getByRole("button", { name: "Review change" }).scrollIntoViewIfNeeded();
          await page.screenshot({
            path: resolve(captures, `${layout}-${String(width)}-${theme}-detail.png`),
            animations: "disabled",
          });
        }
      });
    }
