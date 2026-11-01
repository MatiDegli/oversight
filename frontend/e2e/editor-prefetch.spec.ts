import { expect, test } from "@playwright/test";

import { engaged, installHolds, release } from "./holds";
import { holds } from "./level2-log";
import { interceptCommands, removeRoutes } from "./level2-support";
import {
  editorAssets,
  expectNoEditorBeforeGraph,
  implementation,
  recordEditorStartup,
} from "./editor-startup-support";

test.afterEach(async ({ page }) => removeRoutes(page));

for (const idle of ["native", "timeout fallback"] as const) {
  test(`watch prefetches the shared editor once after drawing, using ${idle}`, async ({ page }) => {
    if (idle === "timeout fallback")
      await page.addInitScript(() => {
        Object.defineProperty(window, "requestIdleCallback", {
          value: undefined,
          configurable: true,
        });
      });
    await page.addInitScript(recordEditorStartup);
    await page.addInitScript(installHolds, [holds.live, holds.decisions]);
    const requested: string[] = [];
    page.on("request", (request) => requested.push(new URL(request.url()).pathname));
    await interceptCommands(page);
    await page.goto("/level2");
    // Editor specs allow a minute because software canvas rendering stretches replay event timers.
    await engaged(page, holds.live, 60_000);
    await expect
      .poll(() => requested.filter((path) => path === `/${implementation}`).length)
      .toBe(1);
    await expectNoEditorBeforeGraph(page);
    if (idle === "native")
      expect(await page.evaluate(() => window.editorStartup)).toMatchObject({
        idleScheduled: 1,
        idleFired: 1,
        idleAfterGraph: true,
      });
    await expect(page.locator(".monaco-editor")).toHaveCount(0);
    await release(page, holds.live);
    await engaged(page, holds.decisions, 60_000);
    expect(requested.filter((path) => path === `/${implementation}`)).toHaveLength(1);
    await page
      .getByRole("article", { name: "Agent A" })
      .getByRole("button", { name: "Review change" })
      .click();
    await expect(page.locator(".monaco-diff-editor")).toBeVisible();
    expect(requested.filter((path) => path === `/${implementation}`)).toHaveLength(1);
  });
}

test("Save-Data skips speculative editor requests after the graph and still permits an open", async ({
  page,
}) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "connection", {
      value: { saveData: true },
      configurable: true,
    });
  });
  await page.addInitScript(recordEditorStartup);
  await page.addInitScript(installHolds, [holds.live, holds.decisions]);
  const requested: string[] = [];
  page.on("request", (request) => requested.push(new URL(request.url()).pathname));
  await interceptCommands(page);
  await page.goto("/level2");
  await engaged(page, holds.live, 60_000);
  await release(page, holds.live);
  await engaged(page, holds.decisions, 60_000);
  expect(requested.filter((path) => editorAssets.has(path))).toEqual([]);
  expect(await page.evaluate(() => window.editorStartup.idleScheduled)).toBe(0);
  await page
    .getByRole("article", { name: "Agent A" })
    .getByRole("button", { name: "Review change" })
    .click();
  await expect(page.locator(".monaco-diff-editor")).toBeVisible();
  await expectNoEditorBeforeGraph(page);
});
