import { fileURLToPath } from "node:url";

import { chromium, expect as browserExpect } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

import type { LiveDiffData } from "../state";
import type {} from "./LiveDiff.browser.fixture";

let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let url: string;
let html: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/editor-vite-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { panelTest } from "/src/editor/LiveDiff.browser.fixture.tsx"; window.panelTest = panelTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
}, 60_000);

it("keeps the disabled find surface on the field token", async () => {
  await withPage(async (page) => {
    await render(page);
    await expectDiff(page, 8);
    await page.evaluate(() => {
      window.panelTest.current()?.getModifiedEditor().focus();
    });
    await page.keyboard.press("Control+f");
    const input = page.locator(".find-widget.visible .monaco-findInput").first();
    await browserExpect(input).toBeVisible();
    const colors = await input.evaluate((element) => {
      // Hold the state Monaco briefly uses while dismissing find, independent of animation timing.
      element.classList.add("disabled");
      const reference = document.createElement("span");
      reference.style.backgroundColor = "var(--field)";
      document.body.append(reference);
      const result = {
        actual: getComputedStyle(element).backgroundColor,
        expected: getComputedStyle(reference).backgroundColor,
      };
      reference.remove();
      return result;
    });
    expect(colors.actual).toBe(colors.expected);
  });
}, 60_000);
afterAll(async () => {
  await browser?.close();
  await server?.close();
});

async function withPage(test: (page: Page) => Promise<void>) {
  if (!browser) throw new Error("Browser was not started");
  const page = await browser.newPage({ viewport: { width: 1200, height: 1000 } });
  page.setDefaultTimeout(15_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  // Vite's client logs that it has no HMR socket here; every other console error fails the test.
  page.on("console", (message) => {
    if (message.type() === "error" && !message.location().url.endsWith("/@vite/client"))
      errors.push(message.text());
  });
  try {
    // Avoid starting the application's transport: only the editor fixture is mounted.
    await page.route("**/", (route) =>
      route.fulfill({
        contentType: "text/html",
        body: html,
      }),
    );
    await page.goto(url);
    await page.waitForFunction(() => "panelTest" in window);
    await test(page);
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
}

const data: LiveDiffData = {
  changeId: "change-1",
  agentId: "agent-1",
  path: "shop/first.py",
  original: Array.from({ length: 51 }, (_, i) => `first_${String(i)} = ${String(i)}`).join("\n"),
  modified: Array.from(
    { length: 51 },
    (_, i) => `first_${String(i)} = ${String(i === 7 ? 99 : i)}`,
  ).join("\n"),
  linesAdded: 1,
  linesRemoved: 1,
  status: "modified",
  author: "agent",
};

async function render(page: Page, input = data) {
  await page.evaluate((value) => {
    window.panelTest.render(value);
  }, input);
}
async function expectDiff(page: Page, line: number, end = line) {
  await browserExpect
    .poll(
      () =>
        page.evaluate(() =>
          window.panelTest
            .current()
            ?.getLineChanges()
            ?.map((change) => [
              change.originalStartLineNumber,
              change.originalEndLineNumber,
              change.modifiedStartLineNumber,
              change.modifiedEndLineNumber,
            ]),
        ),
      { timeout: 15_000 },
    )
    .toEqual([[line, end, line, end]]);
}

async function expectModels(page: Page, count: number) {
  await browserExpect.poll(() => page.evaluate(() => window.panelTest.models())).toBe(count);
}

it("switches selected files and changes in one editor, with exact line changes and no model errors", async () => {
  await withPage(async (page) => {
    await render(page);
    await expectDiff(page, 8);
    const second = {
      ...data,
      path: "shop/second.py",
      original: Array.from({ length: 30 }, (_, i) => `second_${String(i)} = ${String(i)}`).join(
        "\n",
      ),
      modified: Array.from(
        { length: 30 },
        (_, i) => `second_${String(i)} = ${String(i === 13 ? 99 : i)}`,
      ).join("\n"),
    };
    await render(page, second);
    await expectDiff(page, 14);
    await expectModels(page, 2);
    await render(page, {
      ...second,
      modified: second.original.replace("second_20 = 20", "second_20 = 99"),
    });
    await expectDiff(page, 21);
    await render(page, { ...second, changeId: "change-2" });
    await expectDiff(page, 14);
    await render(page);
    await expectDiff(page, 8);
    await expectModels(page, 2);
    expect(await page.evaluate(() => window.panelTest.mounts())).toBe(1);
    await page.evaluate(() => {
      window.panelTest.render(undefined);
    });
    await expectModels(page, 0);
  });
}, 60_000);

it("tints the gutter for a removal but not for an insertion", async () => {
  await withPage(async (page) => {
    const lines = (skip: number | null, extra: number | null) =>
      Array.from({ length: 12 }, (_, i) => `line_${String(i)} = ${String(i)}`)
        .flatMap((line, i) => (i === extra ? [line, "added = 1"] : [line]))
        .filter((_, i) => i !== skip)
        .join("\n");
    // The gutter's own color, as the computed style of an element painted with the token.
    const gutter = () =>
      page.evaluate(() => {
        const probe = document.createElement("div");
        probe.style.backgroundColor = "var(--page)";
        document.body.append(probe);
        const page = getComputedStyle(probe).backgroundColor;
        probe.remove();
        const painted = (selector: string) =>
          [...document.querySelectorAll(selector)].map(
            (element) => getComputedStyle(element).backgroundColor,
          );
        return {
          page,
          // Placeholders in the original gutter beside inserted lines.
          inserted: painted(".original-in-monaco-diff-editor .margin-view-zones .gutter-delete"),
          // Gutter cells of removed lines in the inline view.
          removed: painted(
            ".inline-deleted-margin-view-zone, .margin-view-overlays .gutter-delete",
          ),
        };
      });
    await render(page, { ...data, original: lines(null, null), modified: lines(null, 5) });
    await browserExpect.poll(async () => (await gutter()).inserted.length).toBeGreaterThan(0);
    const insertion = await gutter();
    expect(new Set(insertion.inserted)).toEqual(new Set([insertion.page]));

    await render(page, {
      ...data,
      path: "shop/removal.py",
      original: lines(null, null),
      modified: lines(3, null),
    });
    await browserExpect.poll(async () => (await gutter()).removed.length).toBeGreaterThan(0);
    const removal = await gutter();
    // A removal keeps its tint: its gutter is not the page's color.
    expect(removal.removed.every((color) => color !== removal.page)).toBe(true);
  });
}, 60_000);

it("mounts Monaco only after both font loads resolve", async () => {
  await withPage(async (page) => {
    await page.evaluate(() => {
      window.panelTest.gateFonts();
    });
    await render(page);
    await browserExpect(page.getByRole("status")).toHaveText("Loading editor fonts…");
    expect(await page.evaluate(() => window.panelTest.pendingFonts())).toBe(2);
    expect(await page.evaluate(() => window.panelTest.mounts())).toBe(0);
    await page.evaluate(() => {
      window.panelTest.releaseFont();
    });
    expect(await page.evaluate(() => window.panelTest.mounts())).toBe(0);
    await browserExpect(page.locator(".monaco-diff-editor")).toHaveCount(0);
    await page.evaluate(() => {
      window.panelTest.releaseFont();
    });
    await expectDiff(page, 8);
    expect(await page.evaluate(() => window.panelTest.mounts())).toBe(1);
  });
}, 60_000);

it("shows a recoverable font failure without creating Monaco", async () => {
  await withPage(async (page) => {
    await page.evaluate(() => {
      window.panelTest.gateFonts(true);
    });
    await render(page);
    await browserExpect(page.getByRole("alert")).toHaveText(
      "Editor fonts could not load. Reload to try again.",
    );
    expect(await page.evaluate(() => window.panelTest.mounts())).toBe(0);
  });
}, 60_000);

it("uses only token colors in rendered Monaco, at rest and while reading, finding and scrolling", async () => {
  await withPage(async (page) => {
    const filler = Array.from({ length: 80 }, (_, i) => `value_${String(i)} = price()`).join("\n");
    // The modified string holds a Cyrillic letter, which Monaco's unicode highlight marks.
    await render(page, {
      ...data,
      original: `def price():\n    # Original\n    return [[[1]], 'text']\n${filler}\n`,
      modified: `def price():\n    # Modified\n    return [[[2]], 't\u0435xt']\n${filler}\n`,
    });
    await expectDiff(page, 2, 3); // the two adjacent changed lines form one hunk
    const unknown: string[] = [];
    const collect = async (state: string) => {
      const found = await page.evaluate(() => [
        ...window.panelTest.unknownColors(),
        ...window.panelTest.unknownCanvasColors(),
      ]);
      unknown.push(...found.map((entry) => `${state}: ${entry}`));
    };
    await collect("rest");
    await page.evaluate(() => {
      const editor = window.panelTest.current()?.getModifiedEditor();
      editor?.focus();
      editor?.setPosition({ lineNumber: 3, column: 12 }); // before the outer bracket
    });
    await browserExpect(page.locator(".bracket-match").first()).toBeVisible();
    await collect("bracket match");
    await page.evaluate(() => {
      window.panelTest
        .current()
        ?.getModifiedEditor()
        .setSelection({ startLineNumber: 3, startColumn: 5, endLineNumber: 5, endColumn: 9 });
    });
    await browserExpect(page.locator(".selected-text").first()).toBeVisible();
    await collect("selection");
    await page.evaluate(() => {
      window.panelTest
        .current()
        ?.getModifiedEditor()
        .setSelection({ startLineNumber: 1, startColumn: 5, endLineNumber: 1, endColumn: 10 });
    });
    await page.keyboard.press("Control+f");
    await browserExpect(page.locator(".find-widget.visible")).toBeVisible();
    await browserExpect(page.locator(".findMatch").first()).toBeVisible();
    await collect("find");
    // Regex on and an unclosed bracket: Monaco paints its validation message and input border.
    await page.keyboard.press("Alt+r");
    await browserExpect(page.locator(".find-widget .monaco-custom-toggle.checked")).toHaveCount(1);
    await page.keyboard.type("[");
    await browserExpect(page.locator(".monaco-inputbox-message.error")).toBeVisible();
    await collect("find regex error");
    await page.keyboard.press("Escape");
    await page.evaluate(() => {
      window.panelTest.current()?.getModifiedEditor().setScrollTop(400);
    });
    await page.mouse.move(600, 600);
    await browserExpect(page.locator(".scrollbar.vertical.visible").first()).toBeVisible();
    await collect("scrolled");
    expect(unknown).toEqual([]);
    expect(await page.evaluate(() => window.panelTest.canvasColorCount())).toBeGreaterThan(0);
    const settings = await page.evaluate(() => {
      const { palette, monaco } = window.panelTest;
      const diff = window.panelTest.current();
      const editor = diff?.getModifiedEditor();
      const models = diff?.getModel();
      return {
        modelBrackets: [models?.original, models?.modified].map(
          (model) => model?.getOptions().bracketPairColorizationOptions.enabled,
        ),
        guides: editor?.getOption(monaco.editor.EditorOption.guides),
        font: editor?.getOption(monaco.editor.EditorOption.fontFamily),
        expectedFont: palette["code-font"],
      };
    });
    expect(settings.modelBrackets).toEqual([false, false]);
    expect(settings.guides).toMatchObject({
      bracketPairs: false,
      bracketPairsHorizontal: false,
      highlightActiveBracketPair: false,
    });
    expect(settings.font).toBe(settings.expectedFont);
  });
}, 60_000);
