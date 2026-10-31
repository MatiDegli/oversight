import { mkdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { Browser, Page } from "@playwright/test";
import { expect as check } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { fixtureCacheDir } from "../../shots/browserCache.support";
import { launchBrowser } from "../browserLaunch.support";
import type { mountHeader } from "./ReviewHeader.fixture";

const shots = fileURLToPath(new URL("../../../.shots/review-panel/", import.meta.url));
const exactSketches = new Set([
  "a-dark.png",
  "a-day.png",
  "a-1280-dark.png",
  "b-dark.png",
  "b-day.png",
  "c-dark.png",
  "d-dark.png",
  "d2-dark.png",
  "g-dark.png",
]);
const states = ["a", "b", "c", "d", "d2", "g"] as const;
type ShotState = (typeof states)[number];
// All open files are shop/pricing.py and fold unchanged lines: b, c, d, d2 and g.
// a starts with shop/inventory.py closed, so no Monaco fold is expected.
const folded = new Set<ShotState>(["b", "c", "d", "d2", "g"]);
const cases = states.flatMap((state) =>
  [1920, 1280].flatMap((width) => ["dark", "day"].map((theme) => ({ state, width, theme }))),
);

async function compare(
  browser: Browser,
  product: Buffer,
  state: ShotState,
  width: number,
  theme: string,
) {
  const candidate = `${state}${width === 1280 ? "-1280" : ""}-${theme}.png`;
  const exact = exactSketches.has(candidate);
  const file = exact ? candidate : `${state}-dark.png`;
  const sketch = await readFile(new URL(`../../../docs/sketches/review/${file}`, import.meta.url));
  const report = await browser.newPage({ viewport: { width: 3000, height: 1140 } });
  try {
    await report.setContent(
      `<style>body{margin:0;font:16px sans-serif}.pair{display:flex;align-items:flex-start;gap:24px}img{max-width:none}</style><p>Product: hand-written fixture · ${state}, ${String(width)}, ${theme} / ${exact ? "approved sketch" : "nearest sketch"}: docs/sketches/review/${file}</p><div class="pair"><img alt="Product panel" src="data:image/png;base64,${product.toString("base64")}"><img alt="Approved sketch" src="data:image/png;base64,${sketch.toString("base64")}"></div>`,
    );
    await report
      .locator("img")
      .evaluateAll((images) =>
        Promise.all(images.map((img) => (img as HTMLImageElement).decode())),
      );
    await report.screenshot({
      path: `${shots}${state}-${String(width)}-${theme}-compare.png`,
      fullPage: true,
    });
  } finally {
    await report.close();
  }
}

async function mount(page: Page, state: ShotState, width: number, theme: string) {
  await page.waitForFunction(() => "mountHeader" in window);
  await page.evaluate(
    ({ state, width, theme }) => {
      document.documentElement.dataset.theme = theme;
      const api = window as unknown as { mountHeader: typeof mountHeader };
      api.mountHeader(state === "g" ? "merged" : state, undefined, { files: true });
      const host = document.body.firstElementChild as HTMLElement;
      host.style.width = width === 1280 ? "608px" : "880px";
      host.style.height = "962px";
      (host.firstElementChild as HTMLElement).style.width = "100%";
    },
    { state, width, theme },
  );
  await check(page.locator(".review-panel")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  if (folded.has(state)) {
    // Monaco's wrapper has zero height; its disclosure is the visible fold control.
    await check(
      page.locator('.modified .diff-hidden-lines [title="Show Unchanged Region"]').first(),
    ).toBeVisible();
    if (state !== "g")
      await check(page.getByLabel("Editable review file")).toHaveAttribute("data-state", "clean");
  }
}

describe.skipIf(process.env.REVIEW_PANEL_SHOTS !== "1")("whole review panel captures", () => {
  let server: ViteDevServer, browser: Browser, url: string, html: string;
  beforeAll(async () => {
    server = await createServer({
      root: fileURLToPath(new URL("../../", import.meta.url)),
      cacheDir: fixtureCacheDir(expect.getState().testPath),
      server: { host: "127.0.0.1", port: 0, strictPort: false },
    });
    html = await server.transformIndexHtml(
      "/",
      '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { mountHeader } from "/src/review/ReviewHeader.fixture.tsx"; window.mountHeader = mountHeader;</script></body></html>',
    );
    await server.listen();
    url = server.resolvedUrls?.local[0] ?? "";
    browser = await launchBrowser();
    await mkdir(shots, { recursive: true });
  }, 60_000);
  afterAll(async () => {
    await browser.close();
    await server.close();
  });
  it.each(cases)(
    "captures $state at $width in $theme",
    async ({ state, width, theme }) => {
      const page = await browser.newPage({
        viewport: { width, height: 1080 },
        reducedMotion: "reduce",
      });
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("console", (message) => {
        if (message.type() === "error" && !message.location().url.endsWith("/@vite/client"))
          errors.push(message.text());
      });
      try {
        await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
        await page.goto(url);
        await mount(page, state, width, theme);
        const panel = page.locator(".review-panel");
        await check(panel.locator(".review-file-row > span.ml-auto").first()).toHaveText(
          state === "a" ? "Show diff" : state === "g" ? "Inline diff" : "Editable · Inline diff",
        );
        await check(panel.locator(".review-file-row").first()).toHaveAttribute(
          "aria-expanded",
          String(state !== "a"),
        );
        expect(await panel.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(false);
        expect(
          await panel.evaluate((el) =>
            [...el.querySelectorAll<HTMLElement>("*")].some(
              (child) =>
                child.clientWidth > 0 &&
                !child.closest(".monaco-editor") &&
                child.scrollWidth > child.clientWidth,
            ),
          ),
        ).toBe(false);
        const product = await panel.screenshot({
          path: `${shots}${state}-${String(width)}-${theme}.png`,
        });
        await compare(browser, product, state, width, theme);
        expect(errors).toEqual([]);
      } finally {
        await page.close();
      }
    },
    60_000,
  );
});
