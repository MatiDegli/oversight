import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, expect as browserExpect } from "@playwright/test";
import type { Browser, Locator, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

let server: ViteDevServer, browser: Browser, html: string, url: string;
const shots = fileURLToPath(new URL("../../../.shots/review-bar/", import.meta.url));
const capture = process.env.REVIEW_BAR_SHOTS === "1";
const sketchDir = process.env.REVIEW_BAR_SKETCH_DIR;
const unlocked =
  "If main moved since the agent started, the tests run again on the combined result before the merge.";
const locked = "Approve opens when every answer is Yes.";
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: `${shots}test-cache`,
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import {reviewTest,mountInboxCommands} from "/src/app/review.browser.fixture.tsx";window.reviewTest=reviewTest;window.mountInboxCommands=mountInboxCommands;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
  if (capture) await mkdir(shots, { recursive: true });
}, 60000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

function measure(bar: Locator) {
  return bar.evaluate((bar) => {
    const comment = bar.querySelector("textarea, .cbox");
    const buttons = bar.querySelector(".review-decision-actions, .brow");
    const button = buttons?.firstElementChild;
    const caption = bar.querySelector(".review-decision-caption, .cap");
    if (!comment || !buttons || !button || !caption) throw new Error("Missing bar anatomy");
    const css = getComputedStyle(bar);
    const px = (value: string) => parseFloat(value);
    return {
      paddingTop: px(css.paddingTop),
      paddingRight: px(css.paddingRight),
      paddingBottom: px(css.paddingBottom),
      paddingLeft: px(css.paddingLeft),
      commentHeight: comment.getBoundingClientRect().height,
      commentRadius: px(getComputedStyle(comment).borderRadius),
      buttonsOffset: buttons.getBoundingClientRect().top - comment.getBoundingClientRect().bottom,
      buttonHeight: button.getBoundingClientRect().height,
      buttonGap: px(getComputedStyle(buttons).gap),
      buttonRadius: px(getComputedStyle(button).borderRadius),
      buttonSize: px(getComputedStyle(button).fontSize),
      buttonWeight: px(getComputedStyle(button).fontWeight),
      captionOffset: caption.getBoundingClientRect().top - buttons.getBoundingClientRect().bottom,
      captionSize: px(getComputedStyle(caption).fontSize),
    };
  });
}
const expected = {
  paddingTop: 12,
  paddingRight: 24,
  paddingBottom: 10,
  paddingLeft: 24,
  commentHeight: 64,
  commentRadius: 10,
  buttonsOffset: 10,
  buttonHeight: 40,
  buttonGap: 10,
  buttonRadius: 7,
  buttonSize: 14,
  buttonWeight: 600,
  captionOffset: 8,
  captionSize: 13,
};
const check = (actual: Awaited<ReturnType<typeof measure>>) => {
  for (const key of Object.keys(expected) as (keyof typeof expected)[])
    expect(Math.abs(actual[key] - expected[key]), JSON.stringify(actual)).toBeLessThanOrEqual(1);
};

it.each(["dark", "day"])(
  "contains long failures and preserves multilingual comments and focus in %s",
  async (theme) => {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    try {
      await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
      await page.goto(url);
      await page.waitForFunction(() => "reviewTest" in window);
      await page.evaluate((theme) => {
        document.documentElement.dataset.theme = theme;
        window.reviewTest.mount(false, 20);
      }, theme);
      await page.getByRole("button", { name: /^Decisions:/ }).click();
      await browserExpect(page.getByLabel("Editable review file")).toHaveAttribute(
        "data-state",
        "clean",
      );
      const bar = page.getByRole("region", { name: "Decision", exact: true });
      const comment = bar.getByRole("textbox", { name: "Comment for the agent" });
      const draft = "Revisá 日本語 مرحبا 🙂 " + "x".repeat(200);
      await comment.fill(draft);
      await page.keyboard.press("Control+Enter");
      expect(await page.evaluate(() => window.reviewTest.decisions)).toEqual([]);
      await bar.getByRole("button", { name: "Request changes" }).click();
      for (const button of await bar.getByRole("button").all())
        await browserExpect(button).toBeDisabled();
      await comment.focus();
      const message = "Cannot update " + "workspace_path_".repeat(24) + ": try again.";
      await page.evaluate((message) => {
        window.reviewTest.emit({
          seq: 0,
          ts: "2026-10-30T18:00:00Z",
          type: "error",
          payload: {
            source: "server",
            message,
            change_id: "change-1",
            agent_id: "agent-1",
            fatal: false,
          },
        });
      }, message);
      await browserExpect(bar.getByRole("alert")).toHaveText(message);
      await browserExpect(comment).toHaveValue(draft);
      await browserExpect(comment).toBeFocused();
      await browserExpect(bar.getByRole("button", { name: "Request changes" })).toBeEnabled();
      const size = await bar.evaluate((bar) => ({
        content: bar.scrollWidth,
        available: bar.clientWidth,
      }));
      expect(size.content).toBeLessThanOrEqual(size.available);
      expect(await page.evaluate(() => window.reviewTest.decisions)).toHaveLength(1);
    } finally {
      await page.close();
    }
  },
  60000,
);

async function comparison(
  page: Page,
  name: string,
  theme: string,
  actual: Awaited<ReturnType<typeof measure>>,
) {
  if (!sketchDir) throw new Error("Set REVIEW_BAR_SKETCH_DIR to the read-only sketch directory");
  const product = await page.screenshot({ path: `${shots}${name}-${theme}-panel.png` });
  const header = await page
    .locator(".review-header")
    .screenshot({ path: `${shots}${name}-${theme}-header.png` });
  const sketch = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  try {
    await sketch.route("http://sketch.test/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      await route.fulfill({
        body: await readFile(resolve(sketchDir, `.${path}`)),
        contentType: path.endsWith(".woff2") ? "font/woff2" : "text/html",
      });
    });
    await sketch.goto(`http://sketch.test/review.html?theme=${theme}#${name}`);
    await sketch.evaluate(() => document.fonts.ready);
    const reference = await measure(sketch.locator(".rvbar"));
    check(reference);
    const original = await readFile(
      new URL(`../../../docs/sketches/review/${name}-${theme}.png`, import.meta.url),
    );
    const rows = Object.keys(expected)
      .map((key) => {
        const k = key as keyof typeof expected;
        return `<tr><td>${k}</td><td>${String(actual[k])}</td><td>${String(reference[k])}</td><td>${String(expected[k])}</td></tr>`;
      })
      .join("");
    const report = await browser.newPage({ viewport: { width: 3840, height: 1300 } });
    try {
      await report.setContent(
        `<style>body{margin:0;font:16px sans-serif;background:#eee;color:#111}.pair{display:flex}td,th{padding:2px 20px;text-align:left}</style><div class="pair"><img width="1920" src="data:image/png;base64,${product.toString("base64")}"><img width="1920" src="data:image/png;base64,${original.toString("base64")}"></div><p>Product (hand-written fixture) / approved docs/sketches/review/${name}-${theme}.png · 1920 × 1080 · DESIGN.md item 6</p><table><tr><th>Measurement (px except weight)</th><th>Product</th><th>Sketch DOM</th><th>DESIGN.md</th></tr>${rows}</table>`,
      );
      await report.screenshot({ path: `${shots}${name}-${theme}-compare.png`, fullPage: true });
      await report.setViewportSize({ width: 1810, height: 200 });
      await report.setContent(
        `<style>body{margin:0;display:flex;gap:24px}.crop{position:relative;width:880px;height:131px;overflow:hidden}.crop img{position:absolute;left:-56px;top:-92px;width:1920px}</style><img src="data:image/png;base64,${header.toString("base64")}"><div class="crop"><img src="data:image/png;base64,${original.toString("base64")}"></div>`,
      );
      await report.screenshot({ path: `${shots}${name}-${theme}-header-compare.png` });
    } finally {
      await report.close();
    }
    await writeFile(
      `${shots}${name}-${theme}.json`,
      JSON.stringify({ actual, reference, expected }, null, 2),
    );
  } finally {
    await sketch.close();
  }
}

it.each(["dark", "day"])(
  "measures one pinned decision bar for a and b at 1920 in %s",
  async (theme) => {
    for (const name of ["a", "b"]) {
      const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      try {
        await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
        await page.goto(url);
        await page.waitForFunction(() => "mountInboxCommands" in window);
        await page.evaluate(
          ({ theme, name }) => {
            document.documentElement.dataset.theme = theme;
            if (name === "a")
              (window as unknown as { mountInboxCommands: () => void }).mountInboxCommands();
            else window.reviewTest.mount(false, 20);
          },
          { theme, name },
        );
        await page.getByRole("button", { name: /^Decisions:/ }).click();
        if (name === "a") {
          await page
            .locator(".inbox-row")
            .nth(1)
            .getByRole("button", { name: "Review change" })
            .click();
          await page.getByRole("button", { name: "Yes, keep it" }).first().click();
        } else
          await browserExpect(page.getByLabel("Editable review file")).toHaveAttribute(
            "data-state",
            "clean",
          );
        await page.evaluate(() => document.fonts.ready);
        const bar = page.getByRole("region", { name: "Decision", exact: true });
        await browserExpect(bar).toHaveCount(1);
        await browserExpect(page.locator(".local-decisions")).toHaveCount(0);
        const comment = bar.getByRole("textbox", { name: "Comment for the agent" });
        await browserExpect(comment).toHaveAttribute("placeholder", "Comment for the agent");
        await browserExpect(bar.getByRole("button", { name: "Request changes" })).toBeDisabled();
        const caption = bar.locator(".review-decision-caption");
        await browserExpect(caption).toHaveCount(1);
        await browserExpect(caption).toHaveText(name === "a" ? locked : unlocked);
        const actual = await measure(bar);
        check(actual);
        const geometry = await bar.evaluate((bar) => {
          const panel = bar.parentElement;
          if (!panel) throw new Error("Missing panel");
          return {
            bottom: bar.getBoundingClientRect().bottom,
            panelBottom: panel.getBoundingClientRect().bottom,
            buttons: [...bar.querySelectorAll("button")].map((button) => ({
              height: button.getBoundingClientRect().height,
              radius: getComputedStyle(button).borderRadius,
            })),
          };
        });
        expect(geometry.bottom).toBe(geometry.panelBottom);
        expect(geometry.buttons).toEqual(
          Array.from({ length: 3 }, () => ({ height: 40, radius: "7px" })),
        );
        for (const icon of ["file", "diff", "scan-line"])
          await browserExpect(page.locator(`.review-header .lucide-${icon}`)).toHaveCount(1);
        const approve = bar.getByRole("button", { name: "Approve and merge" });
        if (name === "a") {
          await browserExpect(approve).toBeDisabled();
          await browserExpect(approve.locator(".lucide-lock-keyhole")).toHaveCount(1);
        }
        if (capture) await comparison(page, name, theme, actual);
        if (name === "a") {
          await page.getByRole("button", { name: "Yes, keep it" }).click();
          await browserExpect(approve).toBeEnabled();
          await browserExpect(caption).toHaveText(unlocked);
          await browserExpect(approve.locator(".lucide-lock-keyhole")).toHaveCount(0);
        }
        expect(errors).toEqual([]);
      } finally {
        await page.close();
      }
    }
  },
  60000,
);
