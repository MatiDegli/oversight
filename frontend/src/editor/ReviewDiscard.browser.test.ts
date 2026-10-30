import { expect as browserExpect } from "@playwright/test";
import { expect, it } from "vitest";

import { data, withPage } from "./LiveDiff.browser.support";

it.each(["pending", "settled"])("discards edits with a %s folded diff at EOF", async (phase) => {
  await withPage(async (page) => {
    await page.evaluate((data) => {
      window.panelTest.renderReview(data);
    }, data);
    const review = page.getByLabel("Editable review file");
    await browserExpect(review).toHaveAttribute("data-state", "clean");
    await browserExpect
      .poll(() => page.evaluate(() => window.panelTest.current()?.getLineChanges()?.length))
      .toBe(1);
    const modelsBefore = await page.evaluate(() => ({
      count: window.panelTest.models(),
      original: window.panelTest.current()?.getModel()?.original.id,
      modified: window.panelTest.current()?.getModel()?.modified.id,
    }));
    for (const text of ["\n# person edit\n", "\n# one\n# two\n# three\n"]) {
      const lines = await page.evaluate((text) => {
        const model = window.panelTest.current()?.getModel()?.modified;
        if (!model) throw new Error("Missing modified model");
        const line = model.getLineCount();
        const column = model.getLineMaxColumn(line);
        model.pushEditOperations(
          [],
          [
            {
              range: {
                startLineNumber: line,
                startColumn: column,
                endLineNumber: line,
                endColumn: column,
              },
              text,
            },
          ],
          () => null,
        );
        return model.getLineCount();
      }, text);
      if (phase === "settled")
        await browserExpect
          .poll(() =>
            page.evaluate(
              () => window.panelTest.current()?.getLineChanges()?.at(-1)?.modifiedEndLineNumber,
            ),
          )
          .toBe(lines);
      await review.getByRole("button", { name: "Discard edits", exact: true }).click();
      await browserExpect(review).toHaveAttribute("data-state", "clean");
      await browserExpect
        .poll(() => page.evaluate(() => window.panelTest.current()?.getLineChanges()?.length))
        .toBe(1);
      expect(
        await page.evaluate(() => window.panelTest.current()?.getModel()?.modified.getValue()),
      ).toBe(data.modified);
      expect(
        await page.evaluate(() => ({
          count: window.panelTest.models(),
          original: window.panelTest.current()?.getModel()?.original.id,
          modified: window.panelTest.current()?.getModel()?.modified.id,
        })),
      ).toEqual(modelsBefore);
      await browserExpect(
        page.locator('.modified .diff-hidden-lines [title="Show Unchanged Region"]').first(),
      ).toBeVisible();
    }
  });
});
