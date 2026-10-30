import { expect as browserExpect } from "@playwright/test";
import { expect, it } from "vitest";

import { data, withPage } from "../editor/LiveDiff.browser.support";

it("discards review text without detaching the shared diff models", async () => {
  await withPage(async (page) => {
    await page.evaluate((data) => window.panelTest.renderReview(data), data);
    const review = page.getByLabel("Editable review file");
    await browserExpect(review).toHaveAttribute("data-state", "clean");
    const before = await page.evaluate(() => {
      const diff = window.panelTest.current();
      const model = diff?.getModel();
      if (!diff || !model) throw new Error("Missing diff models");
      const attachments: string[] = [];
      diff.onDidChangeModel(() => {
        attachments.push(diff.getModel()?.modified.id ?? "detached");
        document.body.dataset.attachments = JSON.stringify(attachments);
      });
      document.body.dataset.attachments = "[]";
      model.modified.pushEditOperations(
        [],
        [
          {
            range: { startLineNumber: 8, endLineNumber: 8, startColumn: 1, endColumn: 1 },
            text: "# person edit ",
          },
        ],
        () => null,
      );
      return { original: model.original.id, modified: model.modified.id };
    });
    await review.getByRole("button", { name: "Discard edits", exact: true }).click();
    await browserExpect(review).toHaveAttribute("data-state", "clean");
    expect(await page.evaluate(() => document.body.dataset.attachments)).toBe("[]");
    expect(
      await page.evaluate(() => {
        const model = window.panelTest.current()?.getModel();
        return { original: model?.original.id, modified: model?.modified.id };
      }),
    ).toEqual(before);
    expect(
      await page.evaluate(() => window.panelTest.current()?.getModel()?.modified.getValue()),
    ).toBe(data.modified);
  });
});
