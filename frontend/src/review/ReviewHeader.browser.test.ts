import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";
import type { Browser, Locator, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

let server: ViteDevServer, browser: Browser, html: string, url: string;
const shots = fileURLToPath(new URL("../../../.shots/review-header/", import.meta.url));
const capture = process.env.REVIEW_HEADER_SHOTS === "1";
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: `${shots}test-cache`,
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { mountHeader } from "/src/review/ReviewHeader.fixture.tsx"; window.mountHeader = mountHeader;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
  if (capture) await mkdir(shots, { recursive: true });
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

// The same DOM measurement applies to the product and the read-only sketch.
function measure(header: Locator) {
  return header.evaluate((header) => {
    const row = header.firstElementChild as HTMLElement;
    const badge = row.firstElementChild as HTMLElement;
    const title = header.querySelector("h2") as HTMLElement;
    const caption = header.querySelector("p") as HTMLElement;
    const chips = header.lastElementChild as HTMLElement;
    const chip = chips.querySelector("li, .rvchip") as HTMLElement;
    const style = getComputedStyle(header),
      number = (v: string) => parseFloat(v);
    return {
      paddingTop: number(style.paddingTop),
      paddingRight: number(style.paddingRight),
      paddingBottom: number(style.paddingBottom),
      paddingLeft: number(style.paddingLeft),
      badgeWidth: badge.getBoundingClientRect().width,
      badgeHeight: badge.getBoundingClientRect().height,
      headlineSize: number(getComputedStyle(title).fontSize),
      headlineLineHeight: number(getComputedStyle(title).lineHeight),
      headlineWeight: number(getComputedStyle(title).fontWeight),
      firstRowGap: number(getComputedStyle(row).gap),
      captionSize: number(getComputedStyle(caption).fontSize),
      captionOffset: caption.getBoundingClientRect().top - row.getBoundingClientRect().bottom,
      chipsOffset: chips.getBoundingClientRect().top - caption.getBoundingClientRect().bottom,
      chipHeight: chip.getBoundingClientRect().height,
      chipSize: number(getComputedStyle(chip).fontSize),
      chipWeight: number(getComputedStyle(chip).fontWeight),
      chipRadius: number(getComputedStyle(chip).borderRadius),
      chipGap: number(getComputedStyle(chips.querySelector("ul") ?? chips).gap),
    };
  });
}
const expected = {
  paddingTop: 18,
  paddingRight: 24,
  paddingBottom: 16,
  paddingLeft: 24,
  badgeWidth: 26,
  badgeHeight: 26,
  headlineSize: 20,
  headlineLineHeight: 28,
  headlineWeight: 700,
  firstRowGap: 12,
  captionSize: 13,
  captionOffset: 6,
  chipsOffset: 12,
  chipHeight: 30,
  chipSize: 14,
  chipWeight: 600,
  chipRadius: 7,
  chipGap: 8,
};

async function comparison(
  page: Page,
  name: "a" | "b",
  theme: string,
  actual: Awaited<ReturnType<typeof measure>>,
  product: Buffer,
) {
  const sketch = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  try {
    await sketch.route("http://sketch.test/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      await route.fulfill({
        body: await readFile(`/home/matidegli/hackathon-research/mockups/review-r1${path}`),
        contentType: path.endsWith(".woff2") ? "font/woff2" : "text/html",
      });
    });
    await sketch.goto(`http://sketch.test/review.html?theme=${theme}#${name}`);
    await sketch.evaluate(() => document.fonts.ready);
    const reference = await measure(sketch.locator(".rvh"));
    for (const key of Object.keys(expected) as (keyof typeof expected)[])
      expect(Math.abs(reference[key] - expected[key])).toBeLessThanOrEqual(1);
    const original = await readFile(
      new URL(`../../../docs/sketches/review/${name}-${theme}.png`, import.meta.url),
    );
    const rows = Object.keys(expected)
      .map((key) => {
        const k = key as keyof typeof expected;
        return `<tr><td>${k}</td><td>${String(actual[k])}</td><td>${String(reference[k])}</td><td>${String(expected[k])}</td></tr>`;
      })
      .join("");
    await page.setContent(
      `<style>body{font:16px sans-serif;background:#eee;color:#111;margin:20px}.pair{display:flex;gap:24px}.pair>div{width:880px}.crop{position:relative;width:880px;height:131px;overflow:hidden}.crop img{position:absolute;left:-56px;top:-92px;width:1920px}table{border-collapse:collapse;margin-top:20px}td,th{padding:3px 18px;text-align:left;border-bottom:1px solid #bbb}</style><h1>Review header · ${name} · ${theme} · viewport 1920 × 1080</h1><div class="pair"><div><p>Product · committed hand-written UI fixture</p><img src="data:image/png;base64,${product.toString("base64")}"></div><div><p>Approved sketch · docs/sketches/review/${name}-${theme}.png</p><div class="crop"><img src="data:image/png;base64,${original.toString("base64")}"></div></div></div><table><tr><th>Measurement (px except weight)</th><th>Product</th><th>Sketch DOM</th><th>DESIGN.md</th></tr>${rows}</table>`,
    );
    await page.screenshot({ path: `${shots}${name}-${theme}.png`, fullPage: true });
    await writeFile(
      `${shots}${name}-${theme}.json`,
      JSON.stringify({ actual, reference, expected }, null, 2),
    );
  } finally {
    await sketch.close();
  }
}

it.each(["dark", "day"])(
  "uses one measured header for A, B and merged in %s at 1920",
  async (theme) => {
    for (const name of ["a", "b", "g"] as const) {
      const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      try {
        await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
        await page.goto(url);
        await page.waitForFunction(() => "mountHeader" in window);
        await page.evaluate(
          ({ name, theme }) => {
            document.documentElement.dataset.theme = theme;
            (window as unknown as { mountHeader: (name: string) => void }).mountHeader(name);
          },
          { name, theme },
        );
        await page.locator(".review-header").waitFor();
        await page.evaluate(() => document.fonts.ready);
        expect(await page.locator("aside header").count()).toBe(1);
        const header = page.locator(".review-header");
        expect(await header.getByRole("listitem").count()).toBe(4);
        expect(
          await header.getByRole("img", { name: name === "a" ? "Agent B" : "Agent A" }).count(),
        ).toBe(1);
        expect(await header.getByRole("button", { name: "Close review" }).count()).toBe(1);
        expect(await header.locator(".review-header-status").textContent()).toBe(
          name === "g" ? "Merged" : "Ready for review",
        );
        const actual = await measure(header);
        for (const key of Object.keys(expected) as (keyof typeof expected)[])
          expect(Math.abs(actual[key] - expected[key]), key).toBeLessThanOrEqual(1);
        if (capture && name !== "g")
          await comparison(
            page,
            name,
            theme,
            actual,
            await header.screenshot({ path: `${shots}${name}-${theme}-header.png` }),
          );
        expect(errors).toEqual([]);
      } finally {
        await page.close();
      }
    }
  },
  60_000,
);
