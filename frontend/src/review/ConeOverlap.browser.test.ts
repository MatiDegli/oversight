import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import { openOversight } from "../workspaceView.browser.support";
import { coneStates, type ConeState } from "./ConeOverlap.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/cone-overlap/cache", import.meta.url)),
    optimizeDeps: { entries: ["src/review/ConeOverlap.fixture.tsx"] },
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import {coneHarness} from "/src/review/ConeOverlap.fixture.tsx"; window.coneHarness = coneHarness;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60000);
afterAll(async () => {
  await browser.close();
  await server.close();
});
async function mounted(width: number, test: (page: Page) => Promise<void>) {
  const page = await browser.newPage({
    viewport: { width, height: width === 390 ? 844 : 800 },
    reducedMotion: "reduce",
  });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.setDefaultTimeout(8000);
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => "coneHarness" in window);
    await page.evaluate(() => window.coneHarness.mount("no-peers"));
    if (width >= 1280) {
      await openOversight(page);
      await page.getByRole("button", { name: "Functions", exact: true }).click();
      await page.getByRole("button", { name: "Review change", exact: true }).click();
    }
    await page.locator(".review-body").waitFor();
    await test(page);
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
}
const replace = (page: Page, name: ConeState) =>
  page.evaluate((name) => window.coneHarness.replace(name), name);
it("renders every lifecycle state once and preserves the no-peer DOM", async () => {
  await mounted(1920, async (page) => {
    const body = page.locator(".review-body");
    const original = await body.innerHTML();
    for (const name of coneStates) {
      await replace(page, name);
      const absent = ["no-peers", "merged", "rejected", "stopped", "self-only"].includes(name);
      await check(page.getByRole("region", { name: "Other pending changes" })).toHaveCount(
        absent ? 0 : 1,
      );
      await check(
        page.getByRole("button", { name: "Approve and merge", exact: true }),
      ).toBeDisabled();
      if (name === "missing-graph") await check(body).toContainText("Cannot check Agent B");
      if (name === "partial-graph") await check(body).toContainText("last good parses");
    }
    await replace(page, "no-peers");
    expect(await body.innerHTML()).toBe(original);
  });
});
it.each([1920, 390])(
  "preserves focused controls, scroll, answers and disclosures at %i",
  async (width) => {
    await mounted(width, async (page) => {
      const approve = page.getByRole("button", {
        name: width === 390 ? "Approve" : "Approve and merge",
        exact: true,
      });
      const question = page.getByRole("region", {
        name: "Should this input receive the capped discount?",
        exact: true,
      });
      await check(approve).toBeDisabled();
      await check(question).toHaveCount(1);
      await page.getByRole("button", { name: "Yes, keep it", exact: true }).click();
      await check(approve).toBeEnabled();
      await page.locator(".review-details > details > summary").click();
      await page.locator(".parameter-samples > summary").click();
      const focus =
        width === 390
          ? page.locator(".parameter-samples > summary")
          : page.getByRole("textbox", { name: "Comment for the agent" });
      await focus.focus();
      const scroll = page.locator(width === 390 ? ".pp-main" : ".review-body");
      const before = await scroll.evaluate((node) => {
        (node as HTMLElement).style.maxHeight = "240px";
        node.scrollTop = 80;
        return node.scrollTop;
      });
      expect(before).toBeGreaterThan(0);
      const comparison = await page.locator(".comparison-lines").textContent();
      for (const name of [
        "intersection",
        "several-peers",
        "revision-removed",
        "stopped",
        "no-peers",
      ] as const) {
        await replace(page, name);
        await check(focus).toBeFocused();
        expect(await scroll.evaluate((node) => node.scrollTop)).toBe(before);
        await check(approve).toBeEnabled();
        await check(question).toHaveCount(1);
        expect(await page.locator(".comparison-lines").textContent()).toBe(comparison);
        await check(page.locator(".review-details > details")).toHaveAttribute("open", "");
        await check(page.locator(".parameter-samples")).toHaveAttribute("open", "");
      }
    });
  },
);
it("inserts membership without facts or comparison and never opens Details on arrival", async () => {
  await mounted(1920, async (page) => {
    await replace(page, "intersection");
    await page.evaluate(() => window.coneHarness.evidence(false, false));
    await check(page.locator(".cone-overlap")).toHaveCount(1);
    await replace(page, "intersection");
    await page.evaluate(() => window.coneHarness.evidence(true, false));
    await check(page.locator(".cone-overlap")).toHaveCount(1);
    await check(page.locator(".review-compared")).toHaveCount(0);
    await replace(page, "intersection");
    await check(page.locator(".review-details > details")).not.toHaveAttribute("open");
  });
});
it("wraps escaped long node identities at phone width in day mode", async () => {
  await mounted(390, async (page) => {
    await replace(page, "intersection");
    await page.evaluate(async () => {
      document.documentElement.dataset.theme = "day";
      await window.coneHarness.stress();
    });
    await check(page.locator(".cone-overlap code")).toContainText("<script>");
    expect(
      await page.evaluate(() => {
        const section = document.querySelector<HTMLElement>(".cone-overlap");
        if (!section) throw new Error("Missing cone section");
        return [
          document.documentElement.scrollWidth - innerWidth,
          section.scrollWidth - section.clientWidth,
          getComputedStyle(section).overflowY,
        ];
      }),
    ).toEqual([0, 0, "visible"]);
  });
});
