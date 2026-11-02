import { fileURLToPath } from "node:url";
import { expect as check } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import { prepareFindingFrame, findingMetrics } from "../../shots/reviewFindings";
import type {} from "./reviewFindings.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/review-findings/cache", import.meta.url)),
    optimizeDeps: { entries: ["src/review/reviewFindings.browser.fixture.tsx"] },
    server: { host: "127.0.0.1", port: 0 },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css";</script></body></html>',
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
  const page = await browser.newPage();
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await prepareFindingFrame(page, { state: "baseline", width, theme: "dark" });
    await test(page);
    const metrics = await findingMetrics(page, errors);
    expect(metrics.pageErrors).toEqual([]);
    expect(metrics.overflow).toEqual({ page: 0, evidence: 0 });
  } finally {
    await page.close();
  }
}
it.each(["pointer", "Enter", "Space"])(
  "operates Details with %s and never opens on arrival",
  async (activation) => {
    await mounted(1920, async (page) => {
      const summary = page.locator(".review-details > details > summary"),
        details = page.locator(".review-details > details");
      await check(details).not.toHaveAttribute("open");
      const control = page.getByRole("textbox", { name: "Comment for the agent" });
      await control.focus();
      await page.evaluate(() => {
        window.reviewFindings.replace("all");
      });
      await check(control).toBeFocused();
      await check(details).not.toHaveAttribute("open");
      if (activation === "pointer") await summary.click();
      else {
        await summary.focus();
        await summary.press(activation);
      }
      await check(details).toHaveAttribute("open", "");
      await check(page.getByRole("region", { name: "Test sequence findings" })).toContainText(
        "test_deleted",
      );
      await summary.press("Enter");
      await check(details).not.toHaveAttribute("open");
    });
  },
);
it.each([1920, 390])(
  "preserves open disclosures, focus and scroll through replacement at %i",
  async (width) => {
    await mounted(width, async (page) => {
      const details = page.locator(".review-details > details"),
        summary = details.locator(":scope > summary");
      await summary.click();
      await page.locator(".parameter-samples > summary").click();
      await summary.focus();
      const scrollRegion = page.locator(width < 1280 ? ".pp-main" : ".review-body");
      const scroll = await scrollRegion.evaluate((node) => {
        (node as HTMLElement).style.maxHeight = "240px";
        node.scrollTop = 80;
        return node.scrollTop;
      });
      expect(scroll).toBeGreaterThan(0);
      for (const name of ["all", "empty-incomplete", "baseline"] as const) {
        await page.evaluate((name) => {
          window.reviewFindings.replace(name);
        }, name);
        await check(summary).toBeFocused();
        await check(details).toHaveAttribute("open", "");
        await check(page.locator(".parameter-samples")).toHaveAttribute("open", "");
        expect(await scrollRegion.evaluate((node) => node.scrollTop)).toBe(scroll);
      }
      await page.evaluate(() => {
        window.reviewFindings.revise();
      });
      await check(page.getByRole("region", { name: "Test sequence findings" })).toHaveCount(0);
      await page.evaluate(() => {
        window.reviewFindings.replace("all");
      });
      await check(page.getByRole("region", { name: "Test sequence findings" })).toHaveCount(1);
      await check(details).toHaveAttribute("open", "");
    });
  },
);
it("mounts findings without comparison and preserves a focused Details during relocation", async () => {
  await mounted(1920, async (page) => {
    await page.locator(".review-details > details > summary").click();
    await page.locator(".review-details > details > summary").focus();
    await page.evaluate(() => {
      window.reviewFindings.replace("all", false);
    });
    await check(page.getByRole("region", { name: "What was compared", exact: true })).toHaveCount(
      0,
    );
    await check(page.locator(".review-details > details > summary")).toBeFocused();
    await check(page.locator(".review-details > details")).toHaveAttribute("open", "");
  });
});

it("wraps long escaped evidence at phone width without another scroll area", async () => {
  await mounted(390, async (page) => {
    await page.evaluate(() => {
      window.reviewFindings.replace("all");
      window.reviewFindings.stress();
      document.documentElement.dataset.theme = "day";
    });
    await page.locator(".review-details > details > summary").click();
    const shape = page.getByRole("region", { name: "Structural observations" });
    await check(shape).toContainText("<script>literal text</script>");
    await check(shape.locator("img, script")).toHaveCount(0);
    const metrics = await findingMetrics(page, []);
    expect(metrics.overflowingFindings).toEqual([]);
    expect(await shape.evaluate((node) => getComputedStyle(node).overflowY)).toBe("visible");
  });
});
