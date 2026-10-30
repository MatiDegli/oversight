import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let url: string;
let html: string;

beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/summary/test-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { mountHeader } from "/src/review/ReviewHeader.fixture.tsx"; window.mountHeader = mountHeader; import {reviewTest} from "/src/app/review.browser.fixture.tsx"; window.reviewTest = reviewTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
}, 60_000);

afterAll(async () => {
  await browser?.close();
  await server?.close();
});

const shots = fileURLToPath(new URL("../../../.shots/review-lead/", import.meta.url));
const expected = {
  padding: 24,
  sectionGap: 24,
  leadGap: 24,
  titleSize: 16,
  titleWeight: 700,
  titleLine: 22,
  captionSize: 13,
  captionLine: 18,
  captionGap: 14,
  noteLabel: 13,
  noteSize: 15,
  noteLine: 22,
  noteGap: 4,
  summarySize: 15,
  summaryLine: 22,
  summaryTitle: 14,
  summaryWeight: 600,
  summaryGap: 4,
  boundGap: 4,
  detailsGap: 8,
  detailsHeight: 44,
  fileHeight: 40,
  pathSize: 14,
  countsSize: 14,
  countsWeight: 600,
  pairGap: 8,
  pairRadius: 10,
  pairPadding: 14,
  pairSize: 15,
  pairLine: 21,
  codeSize: 15,
  codeLine: 21,
  inputSize: 14,
  inputLine: 20,
  pairLabel: 13,
};
async function measure(page: Page) {
  return page.locator(".review-panel").evaluate((panel) => {
    const el = (selector: string) => panel.querySelector<HTMLElement>(selector);
    const css = (selector: string, prop: string) => {
      const e = el(selector);
      return e ? parseFloat(getComputedStyle(e).getPropertyValue(prop)) : null;
    };
    const gap = (a: string, b: string) => {
      const x = el(a),
        y = el(b);
      return x && y ? y.getBoundingClientRect().top - x.getBoundingClientRect().bottom : null;
    };
    return {
      padding: css(".review-body", "padding-left"),
      sectionGap: css(".review-agent-note", "margin-top"),
      leadGap: gap("header", ".review-lead"),
      titleSize: css(".review-lead-heading h3", "font-size"),
      titleWeight: css(".review-lead-heading h3", "font-weight"),
      titleLine: css(".review-lead-heading h3", "line-height"),
      captionSize: css(".review-lead-heading > p", "font-size"),
      captionLine: css(".review-lead-heading > p", "line-height"),
      captionGap: css(".review-lead-heading", "gap"),
      noteLabel: css(".review-agent-note > p", "font-size"),
      noteSize: css(".review-agent-note > p + p", "font-size"),
      noteLine: css(".review-agent-note > p + p", "line-height"),
      noteGap: gap(".review-agent-note > p", ".review-agent-note > p + p"),
      summarySize: css(".comparison-lines", "font-size"),
      summaryLine: css(".comparison-lines", "line-height"),
      summaryTitle: css(".review-compared h3", "font-size"),
      summaryWeight: css(".review-compared h3", "font-weight"),
      summaryGap: css(".comparison-lines", "margin-top"),
      boundGap: css(".comparison-bound", "margin-top"),
      detailsGap: css(".review-details", "margin-top"),
      detailsHeight: css(".review-details > details > summary", "height"),
      fileHeight: css(".review-file-row", "height"),
      pathSize: css(".review-file-row > span", "font-size"),
      countsSize: css(".review-file-row > span + span", "font-size"),
      countsWeight: css(".review-file-row > span + span", "font-weight"),
      pairGap: css(".example-pair", "gap"),
      pairRadius: css(".example-pair > div", "border-radius"),
      pairPadding: css(".example-pair > div", "padding-top"),
      pairSize: css(".example-pair > div > code", "font-size"),
      pairLine: css(".example-pair > div > code", "line-height"),
      codeSize: css(".monaco-diff-editor .view-lines", "font-size"),
      codeLine: css(".monaco-diff-editor .view-lines", "line-height"),
      inputSize: css(".choice-example > code", "font-size"),
      inputLine: css(".choice-example > code", "line-height"),
      pairLabel: css(".example-pair > div > span", "font-size"),
    };
  });
}
const cases = [
  ["a", "2 choices need you", "1 of 2 answered", true],
  ["b", "1 caller behaves differently", "3 of 9 callers measured", false],
  [
    "d",
    "Nothing was compared for this change.",
    "Behavior and callers were not recorded for this revision. The diff and tests are the evidence.",
    false,
  ],
  ["merged", "Merged by your rules", "Merge mode: Merge alone when safe", false],
] as const;
it.each(cases)(
  "orders and measures %s at 1920",
  async (name, title, caption, closed) => {
    if (!browser) throw new Error("Missing browser");
    for (const [width, theme] of name === "a"
      ? ([
          [1920, "dark"],
          [1920, "day"],
          [1280, "dark"],
        ] as const)
      : name === "b"
        ? ([
            [1920, "dark"],
            [1920, "day"],
          ] as const)
        : ([[1920, "dark"]] as const)) {
      const page = await browser.newPage({ viewport: { width, height: 1080 } });
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      try {
        await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
        await page.goto(url);
        await page.waitForFunction(() => "mountHeader" in window);
        await page.evaluate(
          ({ name, theme, width }) => {
            document.documentElement.dataset.theme = theme;
            (window as unknown as { mountHeader: (name: string) => void }).mountHeader(name);
            const host = document.body.firstElementChild as HTMLElement;
            host.style.width = width === 1280 ? "608px" : "880px";
            host.style.height = "962px";
            (host.firstElementChild as HTMLElement).style.width = "100%";
          },
          { name, theme, width },
        );
        await page.locator(".review-lead").waitFor();
        await page.evaluate(() => document.fonts.ready);
        if (!closed) await page.locator(".monaco-diff-editor").waitFor();
        expect(await page.locator(".review-lead-heading h3").textContent()).toBe(title);
        expect(await page.locator(".review-lead-heading > p").textContent()).toBe(caption);
        const order = await page
          .locator(".review-body > *")
          .evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")));
        expect(order).toEqual(
          name === "a" || name === "merged"
            ? ["Review lead", "Agent note", "What was compared", "Change files"]
            : name === "b"
              ? ["Review lead", "Agent note", "Change files", "What was compared"]
              : ["Review lead", "Agent note", "Change files"],
        );
        expect(await page.locator(".review-file-row").first().getAttribute("aria-expanded")).toBe(
          String(!closed),
        );
        expect(await page.locator(".review-details > details[open]").count()).toBe(0);
        expect(await page.locator(".review-compared").count()).toBe(name === "d" ? 0 : 1);
        expect(await page.locator(".review-decision-bar").count()).toBe(name === "merged" ? 0 : 1);
        if (name === "merged") {
          expect(await page.locator(".review-merged").textContent()).toBe(
            "Merged into main merge-p",
          );
          expect(await page.locator(".review-file-row").textContent()).not.toContain("Editable");
        }
        const actual = await measure(page);
        const target = {
          ...expected,
          fileHeight: closed ? 44 : 40,
          ...(width === 1280 ? { pairPadding: 9 } : {}),
        };
        for (const key of Object.keys(target) as (keyof typeof target)[])
          if (actual[key] !== null)
            expect(Math.abs((actual[key] ?? 0) - target[key]), key).toBeLessThanOrEqual(1);
        if (name === "b")
          expect(await page.locator(".same-callers").textContent()).toBe(
            "Cart.subtotals and Customer.quote: same result on 12 recorded calls each.",
          );
        if (process.env.REVIEW_LEAD_SHOTS === "1") {
          await mkdir(shots, { recursive: true });
          const key = `${name === "merged" ? "g" : name}${width === 1280 ? "-1280" : ""}-${theme}`;
          const product = await page
            .locator(".review-panel")
            .screenshot({ path: `${shots}${key}-panel.png` });
          const sketch = await readFile(
            new URL(`../../../docs/sketches/review/${key}.png`, import.meta.url),
          );
          const rows = Object.entries(actual)
            .filter(([, v]) => v !== null)
            .map(
              ([k, v]) =>
                `<tr><td>${k}</td><td>${String(v)}</td><td>${String(target[k as keyof typeof target])}</td></tr>`,
            )
            .join("");
          const report = await browser.newPage({ viewport: { width: width * 2, height: 1400 } });
          await report.setContent(
            `<style>body{margin:0;background:#eee;color:#111;font:16px sans-serif}.pair{display:flex;gap:24px}td{padding:2px 16px}img{object-fit:contain;object-position:top left}</style><p>Product · hand-written fixture / docs/sketches/review/${key}.png · DESIGN.md items 2–5</p><div class="pair"><img width="${String(width === 1280 ? 608 : 880)}" src="data:image/png;base64,${product.toString("base64")}"><img width="${String(width)}" src="data:image/png;base64,${sketch.toString("base64")}"></div><table><tr><th>Measurement</th><th>Product</th><th>DESIGN</th></tr>${rows}</table>`,
          );
          await report.screenshot({ path: `${shots}${key}-compare.png`, fullPage: true });
          await writeFile(
            `${shots}${key}.json`,
            JSON.stringify({ actual, expected: target }, null, 2),
          );
          await report.close();
        }
        expect(errors).toEqual([]);
      } finally {
        await page.close();
      }
    }
  },
  60000,
);

it.each([1280, 1920])(
  "preserves the body scroll, editor and focus under long live updates at %i",
  async (width) => {
    if (!browser) throw new Error("Missing browser");
    for (const recorded of [true, false]) {
      const page = await browser.newPage({ viewport: { width, height: 1080 } });
      try {
        await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
        await page.goto(url);
        await page.waitForFunction(() => "reviewTest" in window);
        await page.evaluate((recorded) => {
          window.reviewTest.mount(recorded, 20);
        }, recorded);
        await page.getByRole("button", { name: /^Decisions:/ }).click();
        const panel = page.getByRole("complementary", { name: "Review panel" });
        await panel.locator(".monaco-diff-editor").waitFor();
        const note = "Agent note. ".repeat(200);
        const publish = (summary: string) =>
          page.evaluate((summary) => {
            const proposal = window.reviewTest.state().changes["change-1"]?.proposal;
            if (!proposal) throw new Error("Missing proposal");
            window.reviewTest.emit({
              seq: 0,
              ts: "2026-10-30T18:00:00Z",
              type: "change.proposed",
              payload: { ...proposal, summary },
            });
          }, summary);
        await publish(note);
        const context = panel.getByRole("region", { name: "Review context", exact: true });
        await context.evaluate((el) => {
          el.scrollTop = 100;
        });
        const editor = await panel.locator(".monaco-diff-editor").elementHandle();
        const close = panel.getByRole("button", { name: "Close review" });
        await close.focus();
        await publish(note + "Updated.");
        expect(await close.evaluate((el) => document.activeElement === el)).toBe(true);
        expect(await context.evaluate((el) => el.scrollTop)).toBe(100);
        expect(
          await editor.evaluate((el) => el === document.querySelector(".monaco-diff-editor")),
        ).toBe(true);
        expect(await context.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
        expect(await panel.locator(".review-agent-note > p + p").textContent()).toBe(
          note + "Updated.",
        );
      } finally {
        await page.close();
      }
    }
  },
  60000,
);
