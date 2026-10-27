import { expect as browserExpect } from "@playwright/test";
import { expect, it } from "vitest";

import { data, expectDiff, expectModels, render, withPage } from "./LiveDiff.browser.support";

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
    // Unchanged lines are now collapsed; expand them before checking the scroll surface.
    await page
      .locator('.modified .diff-hidden-lines [title="Show Unchanged Region"]')
      .last()
      .click();
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
