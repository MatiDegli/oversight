import { expect as browserExpect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { expect, it } from "vitest";

import type { LiveDiffData } from "../state";
import { data, render, withPage } from "./LiveDiff.browser.support";

const longOriginal = Array.from(
  { length: 400 },
  (_, i) => `value_${String(i + 1)} = ${String(i + 1)}`,
).join("\n");
const longDiff = (lines: number[]): LiveDiffData => ({
  ...data,
  original: longOriginal,
  modified: longOriginal
    .split("\n")
    .map((text, i) => (lines.includes(i + 1) ? `${text} + 1000` : text))
    .join("\n"),
});
async function expectInView(page: Page, line: number) {
  await browserExpect
    .poll(() =>
      page.evaluate((line) => {
        const editor = window.panelTest.current()?.getModifiedEditor();
        const position = editor?.getScrolledVisiblePosition({ lineNumber: line, column: 1 });
        return (
          !!position &&
          position.top >= 0 &&
          position.top + position.height <= (editor?.getLayoutInfo().height ?? 0)
        );
      }, line),
    )
    .toBe(true);
}
async function expectReveals(page: Page, lines: number[]) {
  await browserExpect
    .poll(() => page.evaluate(() => window.panelTest.reveals()))
    .toEqual(lines.map((line) => ({ line, immediate: true })));
}

it.each(["live", "review"] as const)(
  "opens %s at the first hunk with expandable unchanged regions and wrap",
  async (mode) => {
    await withPage(async (page) => {
      await page.evaluate(
        ({ mode, data }) => {
          window.panelTest.resize(520, 420);
          if (mode === "live") window.panelTest.render(data);
          else window.panelTest.renderReview(data);
        },
        { mode, data: longDiff([80, 200, 320]) },
      );
      await expectInView(page, 80);
      const folds = page.locator('.modified .diff-hidden-lines [title="Show Unchanged Region"]');
      await browserExpect(folds.first()).toBeVisible();
      expect(await folds.count()).toBeGreaterThan(1);
      const settings = await page.evaluate(() => {
        const editor = window.panelTest.current()?.getModifiedEditor();
        const option = window.panelTest.monaco.editor.EditorOption;
        return { wrap: editor?.getOption(option.wordWrap), focus: editor?.hasWidgetFocus() };
      });
      expect(settings).toEqual({ wrap: "on", focus: false });
      // Monaco's own disclosure restores context on demand.
      await folds.first().click();
      await browserExpect(
        page.locator(".modified .view-line").filter({ hasText: "value_1 = 1" }),
      ).toBeVisible();
    });
  },
  60_000,
);

it("follows the latest edited hunk, including edits above an existing later hunk", async () => {
  await withPage(async (page) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.evaluate(() => {
      window.panelTest.resize(520, 420);
    });
    await render(page, longDiff([80]));
    await expectReveals(page, [80]);
    await render(page, longDiff([80, 320]));
    await expectReveals(page, [80, 320]);
    await expectInView(page, 320);
    const earlier = longDiff([80, 320]);
    earlier.modified = earlier.modified.replace("value_80 = 80 + 1000", "value_80 = 80 + 2000");
    await render(page, earlier);
    await expectReveals(page, [80, 320, 80]);
    await expectInView(page, 80);
  });
}, 60_000);

it.each(["wheel", "scrollbar", "cursor"] as const)(
  "keeps the person's place after %s interaction",
  async (interaction) => {
    await withPage(async (page) => {
      await page.evaluate(() => {
        window.panelTest.resize(520, 420);
      });
      await render(page, longDiff([80, 200, 320]));
      await expectReveals(page, [80]);
      await page.evaluate((interaction) => {
        const diff = window.panelTest.current();
        const editor = diff?.getModifiedEditor();
        editor?.setPosition({ lineNumber: 200, column: 5 });
        editor?.revealLineInCenter(200, window.panelTest.monaco.editor.ScrollType.Immediate);
        if (interaction === "cursor") editor?.focus();
      }, interaction);
      if (interaction === "wheel") {
        const surface = page.locator(".modified .view-lines:not(.line-delete)");
        await surface.hover();
        await page.mouse.wheel(0, 40);
      } else if (interaction === "scrollbar") {
        const slider = page.locator(".modified .scrollbar.vertical .slider");
        await slider.hover();
        await page.mouse.down();
        await page.mouse.move(480, 190);
        await page.mouse.up();
      } else {
        // The live diff stays read-only even when the person types or navigates in it.
        await page.keyboard.press("ArrowRight");
        await page.keyboard.type("human input");
        expect(
          await page.evaluate(() => window.panelTest.current()?.getModel()?.modified.getValue()),
        ).toBe(longDiff([80, 200, 320]).modified);
      }
      const before = await page.evaluate(() =>
        window.panelTest.current()?.getModifiedEditor().getPosition(),
      );
      const next = longDiff([80, 200, 320]);
      next.modified = next.modified.replace("value_321 = 321", "value_321 = 321 + 2000");
      await render(page, next);
      await browserExpect
        .poll(() =>
          page.evaluate(() => window.panelTest.current()?.getModel()?.modified.getValue()),
        )
        .toBe(next.modified);
      // Wait for the worker and the reveal frame, not just the synchronous model replacement.
      await browserExpect
        .poll(() =>
          page.evaluate(
            () => window.panelTest.current()?.getLineChanges()?.at(-1)?.modifiedEndLineNumber,
          ),
        )
        .toBe(321);
      await page.evaluate(
        () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
      );
      expect(await page.evaluate(() => window.panelTest.reveals())).toEqual([
        { line: 80, immediate: true },
      ]);
      expect(
        await page.evaluate(() => window.panelTest.current()?.getModifiedEditor().getPosition()),
      ).toEqual(before);
    });
  },
  60_000,
);

it("resumes on a fresh edit after the pause, but keeps a focused cursor safe beyond the pause", async () => {
  await withPage(async (page) => {
    await page.evaluate(() => {
      window.panelTest.resize(520, 420);
    });
    await render(page, longDiff([80]));
    await expectReveals(page, [80]);
    await page.clock.install();
    await page.locator(".modified .view-lines:not(.line-delete)").hover();
    await page.mouse.wheel(0, 40);
    await render(page, longDiff([80, 200]));
    await browserExpect
      .poll(() => page.evaluate(() => window.panelTest.current()?.getLineChanges()?.length))
      .toBe(2);
    await page.clock.fastForward(6_000);
    await expectReveals(page, [80]); // No deferred jump when the pause expires.
    await render(page, longDiff([80, 200, 320]));
    await expectReveals(page, [80, 320]);
    await page.evaluate(() => {
      window.panelTest.current()?.getModifiedEditor().focus();
    });
    await page.keyboard.press("ArrowRight");
    await page.clock.fastForward(6_000);
    await render(page, longDiff([80, 200, 320, 380]));
    await browserExpect
      .poll(() => page.evaluate(() => window.panelTest.current()?.getLineChanges()?.length))
      .toBe(4);
    await page.clock.fastForward(100);
    await expectReveals(page, [80, 320]);
  });
}, 60_000);

it.each(["start", "end"] as const)(
  "opens a pure deletion at the %s of a file",
  async (edge) => {
    await withPage(async (page) => {
      const lines = longOriginal.split("\n");
      if (edge === "start") lines.shift();
      else lines.pop();
      await render(page, { ...data, original: longOriginal, modified: lines.join("\n") });
      await expectReveals(page, [edge === "start" ? 1 : 399]);
      await expectInView(page, edge === "start" ? 1 : 399);
      await browserExpect(
        page.locator(".modified .inline-deleted-margin-view-zone .delete-sign").first(),
      ).toBeVisible();
    });
  },
  60_000,
);

it.each(["live", "review"] as const)(
  "wraps long %s lines with + and - aligned to every visual row",
  async (mode) => {
    await withPage(async (page) => {
      const input = {
        ...data,
        original: `def values():\n    return [${Array.from({ length: 24 }, () => '"original"').join(", ")}]\n`,
        modified: `def values():\n    return [${Array.from({ length: 24 }, () => '"modified"').join(", ")}]\n`,
      };
      await page.evaluate(
        ({ mode, input }) => {
          window.panelTest.resize(520, 900);
          if (mode === "live") window.panelTest.render(input);
          else window.panelTest.renderReview(input);
        },
        { mode, input },
      );
      await browserExpect(
        page.locator(".modified .inline-deleted-margin-view-zone .delete-sign").first(),
      ).toBeVisible();
      const rows = await page.evaluate(() => {
        const positions = (selector: string) =>
          [...document.querySelectorAll(selector)].map((element) => {
            const rect = element.getBoundingClientRect();
            return { top: Math.round(rect.top), height: Math.round(rect.height) };
          });
        const diff = window.panelTest.current();
        const editor = diff?.getModifiedEditor();
        return {
          removed: positions(".modified .view-lines.line-delete .view-line"),
          minus: positions(".modified .inline-deleted-margin-view-zone .delete-sign"),
          added: positions(".modified .view-overlays .line-insert"),
          plus: positions(".modified .margin-view-overlays .insert-sign"),
          scrollWidth: editor?.getScrollWidth(),
          width: editor?.getLayoutInfo().contentWidth,
        };
      });
      expect(rows.removed.length).toBeGreaterThan(1);
      expect(rows.minus).toEqual(rows.removed);
      expect(rows.added.length).toBeGreaterThan(1);
      expect(rows.plus).toEqual(rows.added);
      expect(rows.scrollWidth).toBeLessThanOrEqual(rows.width ?? 0);
    });
  },
  60_000,
);
