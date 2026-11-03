import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check } from "@playwright/test";
import type { Browser, Locator, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type {} from "./ConfigurationAttribution.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
const shots = fileURLToPath(new URL("../../../.shots/configuration-attribution/", import.meta.url));
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: `${shots}cache`,
    optimizeDeps: { entries: ["src/review/ConfigurationAttribution.browser.fixture.tsx"] },
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import "/src/review/ConfigurationAttribution.browser.fixture.tsx";</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
  await mkdir(shots, { recursive: true });
}, 60000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

async function mounted(width: number, run: (page: Page) => Promise<void>) {
  const page = await browser.newPage({
    viewport: { width, height: 1080 },
    reducedMotion: "reduce",
  });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => "configurationAttribution" in window);
    await run(page);
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
}

async function verifyTextLayout(paragraphs: Locator, summary: Locator, width: number) {
  const metrics = await paragraphs.evaluateAll((nodes) =>
    nodes.map((node) => {
      const style = getComputedStyle(node),
        rect = node.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(node);
      return {
        font: style.fontFamily,
        size: style.fontSize,
        line: style.lineHeight,
        weight: style.fontWeight,
        muted: style.color === getComputedStyle(node.parentElement ?? node).color,
        overflow: node.scrollWidth - node.clientWidth,
        clipped: [...range.getClientRects()].some(
          (r) => r.left < rect.left - 1 || r.right > rect.right + 1 || r.bottom > rect.bottom + 1,
        ),
        left: rect.left,
        right: rect.right,
      };
    }),
  );
  for (const metric of metrics) {
    expect(metric.font).toContain("Atkinson Hyperlegible Next");
    expect(metric).toMatchObject({
      size: "15px",
      line: "22px",
      weight: "400",
      muted: true,
      overflow: 0,
      clipped: false,
    });
    if (width === 390) {
      expect(metric.left).toBeGreaterThanOrEqual(16);
      expect(metric.right).toBeLessThanOrEqual(374);
    }
  }
  expect(await summary.evaluate((node) => node.getBoundingClientRect().height)).toBe(44);
}

async function verifyReplayRoundTrip(
  page: Page,
  details: Locator,
  summary: Locator,
  paragraphs: Locator,
  texts: string[],
) {
  const evidence = await page.evaluate(() => window.configurationAttribution.evidence());
  expect(evidence).toHaveLength(1);
  expect(evidence[0]?.complete).toBe(false);
  await page.evaluate(() => {
    window.configurationAttribution.seek(false);
  });
  await check(details).toHaveCount(0);
  expect(await page.evaluate(() => window.configurationAttribution.evidence())).toEqual([]);
  await page.evaluate(() => {
    window.configurationAttribution.seek(true);
  });
  await check(details).not.toHaveAttribute("open");
  await summary.click();
  expect(await paragraphs.allTextContents()).toEqual(
    texts.map((text) => `Configuration attribution: ${text}`),
  );
  expect(await page.evaluate(() => window.configurationAttribution.evidence())).toEqual(evidence);
  await page.evaluate(() => {
    window.configurationAttribution.restart();
  });
  await check(details).toHaveCount(0);
  expect(await page.evaluate(() => window.configurationAttribution.evidence())).toEqual([]);
  await page.evaluate(() => {
    window.configurationAttribution.seek(true);
  });
  await check(details).not.toHaveAttribute("open");
  expect(await page.evaluate(() => window.configurationAttribution.evidence())).toEqual(evidence);
}

it.each([1440, 390])("pins the unmodified real recording HTML at %i", async (width) => {
  await mounted(width, async (page) => {
    await page.evaluate(() => window.configurationAttribution.mount("recorded"));
    const html = await page.locator("[data-baseline]").innerHTML();
    expect(createHash("sha256").update(html).digest("hex")).toMatchInlineSnapshot(
      `"6beacbc7daad667ee199883aeb71d1fe20f0883a8b0fea487745d91d5016c1d6"`,
    );
    await check(page.getByText("Recorded run · Oct 27, 2026", { exact: true })).toBeVisible();
    expect(html).not.toContain("Configuration attribution:");
    const body = await page.locator(".review-body").innerHTML();
    // Captured with ReviewFacts and reviewFindings restored from b378936e.
    expect(createHash("sha256").update(body).digest("hex")).toBe(
      width === 1440
        ? "7649aa22901cb763606e8492ab24efd635998315f1c5be80c0e2e6cb80d387d7"
        : "96134db4f4fb1659742eba537279ad4f3fb799289431ab18784bf84be46e7fff",
    );
  });
});

it.each(
  [1440, 390].flatMap((width) =>
    ["dark", "day"].flatMap((theme) =>
      ["alone", "mixed"].map((mode) => ({ width, theme, mode: mode as "alone" | "mixed" })),
    ),
  ),
)(
  "replays $mode attribution at $width in $theme",
  async ({ width, theme, mode }) => {
    await mounted(width, async (page) => {
      await page.evaluate(
        async ({ theme, mode }) => {
          document.documentElement.dataset.theme = theme;
          await window.configurationAttribution.mount(mode);
          await document.fonts.ready;
        },
        { theme, mode },
      );
      const details = page.locator(".review-details > details"),
        summary = details.locator(":scope > summary");
      await check(details).not.toHaveAttribute("open");
      await summary.click();
      const paragraphs = details.locator("p").filter({ hasText: /^Configuration attribution: / });
      await check(paragraphs).toHaveCount(2);
      const texts = await page.evaluate(() => [
        window.configurationAttribution.diagnostic,
        window.configurationAttribution.secondDiagnostic,
      ]);
      expect(await paragraphs.allTextContents()).toEqual(
        texts.map((text) => `Configuration attribution: ${text}`),
      );
      await check(details.locator("script, img, [role=alert], [role=status]")).toHaveCount(0);
      const incomplete = details.locator("p").filter({ hasText: /^Incomplete:/ });
      if (mode === "mixed")
        await check(incomplete).toHaveText(
          "Incomplete: detector unknown: timeout; source unavailable",
        );
      else await check(incomplete).toHaveCount(0);
      const baseline = await page.locator("[data-baseline]").innerHTML();
      expect(baseline).toContain(
        mode === "alone" ? ">Review signals</summary>" : ">Review signals · Incomplete</summary>",
      );
      await verifyTextLayout(paragraphs, summary, width);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBe(0);
      await summary.evaluate((node) => {
        node.scrollIntoView({ block: "start" });
      });
      if (mode === "mixed") await check(incomplete).toBeVisible();
      await page.screenshot({ path: `${shots}${mode}-${String(width)}-${theme}.png` });
      if (width === 1440 && theme === "dark") {
        const events = await page.evaluate(() => window.configurationAttribution.recording());
        await writeFile(
          `${shots}${mode}-synthetic.jsonl`,
          events.map((event) => JSON.stringify(event)).join("\n"),
        );
      }
      await verifyReplayRoundTrip(page, details, summary, paragraphs, texts);
    });
  },
  60000,
);
