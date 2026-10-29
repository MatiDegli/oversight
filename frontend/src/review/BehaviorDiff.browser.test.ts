import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";
import type { Browser } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

import { behaviorFacts, required } from "./behavior.fixture";

let server: ViteDevServer;
let browser: Browser;
let cache: string;
let html: string;
let url: string;
const shots = fileURLToPath(new URL("../../../.shots/behavior-review/", import.meta.url));
beforeAll(async () => {
  cache = await mkdtemp(join(tmpdir(), "behavior-review-"));
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: cache,
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { reviewTest } from "/src/app/review.browser.fixture.tsx"; window.reviewTest = reviewTest;</script></body></html>',
  );
  await server.listen();
  url = required(server.resolvedUrls?.local[0]);
  browser = await chromium.launch();
  if (process.env.BEHAVIOR_SHOTS === "1") await mkdir(shots, { recursive: true });
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
  if (cache) await rm(cache, { recursive: true, force: true });
});

const cases = [1920, 1280].flatMap((width) =>
  ["dark", "day"].flatMap((theme) =>
    [true, false].flatMap((summary) =>
      ["available", "mixed", "absent"].map((kind, index) => ({
        width,
        height: width === 1920 ? 1080 : 800,
        theme,
        summary,
        kind,
        index,
      })),
    ),
  ),
);
it.each(cases)(
  "keeps bounded $kind evidence usable: $theme $width summary=$summary",
  async ({ width, height, theme, summary, kind, index }) => {
    const page = await browser.newPage({
      viewport: { width, height },
      deviceScaleFactor: 1,
      reducedMotion: "reduce",
    });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    try {
      await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
      await page.goto(url);
      await page.waitForFunction(() => "reviewTest" in window);
      const facts = {
        ...required(behaviorFacts[index]),
        summary: summary ? required(behaviorFacts[index]).summary : null,
      };
      await page.evaluate(
        ({ theme, facts }) => {
          document.documentElement.dataset.theme = theme;
          window.reviewTest.mount(true, 20);
          window.reviewTest.emit({
            seq: 0,
            ts: "2026-10-29T15:00:00Z",
            type: "change.facts_recorded",
            payload: facts,
          });
        },
        { theme, facts },
      );
      await page.getByRole("button", { name: /^Decisions:/ }).click();
      const panel = page.getByRole("complementary", { name: "Review panel" });
      await panel.locator(".line-insert").first().waitFor();
      await page.evaluate(async () => {
        await document.fonts.ready;
      });
      const geometry = () =>
        panel.evaluate((panel) => {
          const bounds = (selector: string) => {
            const element = panel.querySelector(selector);
            if (!element) throw new Error(`Missing ${selector}`);
            const { x, y, width, height } = element.getBoundingClientRect();
            return { x, y, width, height };
          };
          return {
            close: bounds('[aria-label="Close review"]'),
            decisions: bounds('[aria-label="Decision"]'),
            diff: bounds('[aria-label^="Diff of"] > div'),
            panel: panel.getBoundingClientRect().toJSON() as { width: number },
            overflow: panel.scrollWidth > panel.clientWidth,
          };
        });
      const before = await geometry();
      expect(before.diff.height).toBeGreaterThanOrEqual(192);
      expect(before.diff.y + before.diff.height).toBeLessThanOrEqual(before.decisions.y);
      expect(before.decisions.y + before.decisions.height).toBeLessThanOrEqual(height);
      expect(before.overflow).toBe(false);
      expect(before.panel.width).toBeLessThanOrEqual(width / 2);
      const behavior = panel.getByRole("region", { name: "Behavior diff", exact: true });
      if (kind === "absent") {
        expect(await behavior.count()).toBe(0);
        expect(await panel.textContent()).toContain(
          "Behavior evidence was not recorded for this revision.",
        );
        await panel
          .getByText("Behavior evidence was not recorded for this revision.", { exact: true })
          .scrollIntoViewIfNeeded();
      } else {
        expect(await behavior.textContent()).toContain(
          "These bounded inputs do not establish equivalence.",
        );
        expect(await behavior.textContent()).not.toMatch(/Same.result/);
        expect(await behavior.locator("details[open]").count()).toBe(0);
        if (kind === "mixed") {
          expect(await behavior.textContent()).toContain("Methods and classes are not probed yet.");
          expect(await behavior.textContent()).toContain("removed.module:old_function");
        }
        await behavior.evaluate((element) => {
          element.scrollIntoView({ block: "start" });
        });
      }
      if (process.env.BEHAVIOR_SHOTS === "1" && (summary || kind === "mixed")) {
        await page.screenshot({
          path: join(
            shots,
            `review-${kind}-${theme}-${String(width)}${summary ? "" : "-no-summary"}.png`,
          ),
        });
      }
      if (kind !== "absent") {
        await behavior.locator("summary").first().click();
        expect(
          await behavior
            .locator("dt")
            .filter({ hasText: /^Input$/ })
            .count(),
        ).toBe(kind === "available" ? 8 : 1);
        if (kind === "mixed")
          expect(await behavior.textContent()).toContain(
            "Results varied between runs; this is not an established difference.",
          );
        const lossless = await behavior
          .locator("dd")
          .evaluateAll((elements) =>
            elements.every((element) => element.scrollWidth <= element.clientWidth),
          );
        expect(lossless).toBe(true);
        if (process.env.BEHAVIOR_SHOTS === "1" && summary && width === 1920) {
          await behavior.locator("dl").first().scrollIntoViewIfNeeded();
          await page.screenshot({ path: join(shots, `review-${kind}-${theme}-1920-samples.png`) });
        }
      }
      // Updating evidence must keep the person's comment focus and leave decisions in place.
      const close = panel.getByRole("button", { name: "Close review", exact: true });
      await close.focus();
      await page.evaluate(
        (facts) => {
          window.reviewTest.emit({
            seq: 0,
            ts: "2026-10-29T15:01:00Z",
            type: "change.facts_recorded",
            payload: facts,
          });
        },
        {
          ...facts,
          behavior_diff: [
            {
              ...required(required(required(behaviorFacts[0]).behavior_diff)[0]),
              rows: [
                {
                  inputs: "<script> & " + "x".repeat(4000),
                  before: 'exception:ValueError("<bad>")',
                  after: 'str:"value"',
                  unstable: true,
                },
              ],
            },
          ],
        },
      );
      expect(await close.evaluate((element) => document.activeElement === element)).toBe(true);
      const samples = panel.getByRole("region", { name: "Behavior diff", exact: true });
      await samples.locator("summary").click();
      expect(await samples.locator("dd").first().textContent()).toBe(
        "<script> & " + "x".repeat(4000),
      );
      expect(
        await samples
          .locator("dd")
          .evaluateAll((elements) =>
            elements.every((element) => element.scrollWidth <= element.clientWidth),
          ),
      ).toBe(true);
      expect(await page.locator("script:not([type=module])").count()).toBe(0);
      const after = await geometry();
      expect(after.close).toEqual(before.close);
      expect(after.decisions).toEqual(before.decisions);
      expect(after.diff.height).toBeGreaterThanOrEqual(192);
      expect(after.diff.y + after.diff.height).toBeLessThanOrEqual(after.decisions.y);
      expect(after.overflow).toBe(false);
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  },
  60_000,
);
